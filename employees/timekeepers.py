"""Табелчилар: ҳар бир цех учун битта ходим.

Табелчини ES да эмас, БОШҚА ТИЗИМ тайинлайди (API: timekeeper_api.py, ҳужжат: API_TIMEKEEPER.md); ES да тайинлаш саҳифаси йўқ.
Тайинлаш ва олиб ташлаш фақат шу ерда (Timekeeper ёзуви).

Қоидалар
  * Табелчи — тайинланган цехнинг ўз фаол ходими. «Раҳбарият» цехи ходими табелчи бўла олмайди.
  * Ходим фақат битта цехнинг табелчиси бўла олади; цехнинг табелчиси битта (янги тайинлаш эскисини алмаштиради).
  * ES ҳисоби (логин, парол, рол) билан боғлиқ эмас: тайинлов уни на талаб қилади, на ўзгартиради. Табелчи ES га кирмайди,
    фақат бошқа тизимда ишлайди (у ерда E-IMZO билан текширилади); ES ҳисоб яратмайди.
"""
from typing import NamedTuple

from django.db import models, transaction
from django.utils import timezone

from .models import Department, Employee, Timekeeper
from .staff import is_leadership_department, staff_tree_order


class AssignmentResult(NamedTuple):
    assignment: Timekeeper
    replaced: Employee | None   # алмашган эски табелчи (ходим)
    changed: bool               # False — шу ходим аллақачон шу цехнинг табелчиси эди


# Тайинлаш хатоларининг кодлари (HTTP 409). Ҳар бири API_TIMEKEEPER.md да тавсифланган (тест текширади).
# «privileged_account» (2026-11-26 гача) олиб ташланди: тайинлов ҳисоб ролига тегмайди, администратор/HR ходими ҳам табелчи бўла олади.
ASSIGNMENT_ERROR_CODES = (
    'employee_inactive', 'leadership_department', 'already_timekeeper_elsewhere', 'employee_not_in_department',
)


class TimekeeperError(Exception):
    """Тайинлаш мумкин эмас. code — интеграция учун барқарор белги, message — одам учун матн (ўзгариши мумкин)."""

    def __init__(self, message, code, extra=None):
        if code not in ASSIGNMENT_ERROR_CODES:
            raise ValueError(f'Номаълум тайинлаш хато коди: {code!r} (ASSIGNMENT_ERROR_CODES ва API_TIMEKEEPER.md га қўшинг)')
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra or {}


def active_assignment(tabel_number):
    """Шу табель рақамли ходимнинг ишлаб турган табелчилик тайинлови (Timekeeper) ёки None.

    «Ишлаб турган»: ходим ўчирилмаган ва фаол. Бошқа тизим табелчи номидан иш қилганда ES шуни текширади.
    """
    tabel_number = str(tabel_number or '').strip()
    if not tabel_number:
        return None
    return (
        Timekeeper.objects.select_related('department', 'employee')
        .filter(employee__tabel_number=tabel_number, employee__is_deleted=False, employee__is_active=True)
        .first()
    )


def timekeeper_brief(assignment):
    """Тайинлов ҳақида маълумот (API жавоби учун)."""
    employee = assignment.employee
    return {
        'employee_id': employee.id,
        'tabel_number': employee.tabel_number,
        'full_name': str(employee),
        'position': employee.position,
        'is_active': bool(employee.is_active and not employee.is_deleted),   # False бўлса, у номидан иш қилиб бўлмайди
        'assigned_at': timezone.localtime(assignment.created_at).isoformat(),
    }


def assignments_payload(*, assigned=None, tabel_number=''):
    """Барча цехлар (штат дарахтидаги тартибда) ва уларнинг табелчиси (йўқ бўлса — None).

    assigned: True — фақат табелчиси борлари, False — фақат йўқлари; tabel_number — шу ходимнинг цехи.
    """
    assignments = {
        item.department_id: item
        for item in Timekeeper.objects.select_related('employee')
    }
    counts = dict(
        Employee.objects.filter(is_deleted=False, is_active=True, department__isnull=False)
        .values_list('department_id').annotate(total=models.Count('id'))
    )
    payload = []
    for department in sorted(Department.objects.all(), key=staff_tree_order):
        assignment = assignments.get(department.id)
        if assigned is not None and bool(assignment) != assigned:
            continue
        if tabel_number and (assignment is None or assignment.employee.tabel_number != tabel_number):
            continue
        payload.append(department_assignment(department, assignment, employee_count=counts.get(department.id, 0)))
    return payload


def department_assignment(department, assignment, *, employee_count=None):
    item = {
        'department': {'id': department.id, 'name': department.name},
        'timekeeper': timekeeper_brief(assignment) if assignment is not None else None,
    }
    if employee_count is not None:
        item['employee_count'] = employee_count
    return item


@transaction.atomic
def assign_timekeeper(department, employee):
    """Цехга табелчи тайинлаш (олдингиси бўлса — алмашади). Қайтади: AssignmentResult.

    Хато бўлса TimekeeperError ва ҳеч нарса ўзгармайди (транзакция).
    """
    if employee.is_deleted or not employee.is_active:
        raise TimekeeperError('Фаол бўлмаган ходимни табелчи қилиб бўлмайди.', 'employee_inactive')
    if is_leadership_department(employee.department):
        raise TimekeeperError('«Раҳбарият» цехи ходимини табелчи қилиб бўлмайди.', 'leadership_department')
    if is_leadership_department(department):
        raise TimekeeperError('«Раҳбарият» цехига табелчи тайинланмайди.', 'leadership_department')
    other = Timekeeper.objects.select_related('department').filter(employee=employee).exclude(department=department).first()
    if other is not None:
        raise TimekeeperError(
            f'Бу ходим аллақачон «{other.department.name}» цехининг табелчиси.', 'already_timekeeper_elsewhere',
            {'department': {'id': other.department_id, 'name': other.department.name}},
        )
    if employee.department_id != department.id:
        raise TimekeeperError('Табелчи шу цехнинг ўз ходими бўлиши керак.', 'employee_not_in_department')

    current = Timekeeper.objects.select_related('employee').filter(department=department).first()
    if current is not None and current.employee_id == employee.id:
        return AssignmentResult(current, None, False)
    replaced = None
    if current is not None:
        replaced = current.employee
        current.delete()
    return AssignmentResult(Timekeeper.objects.create(department=department, employee=employee), replaced, True)


@transaction.atomic
def remove_timekeeper(department):
    """Цехнинг табелчисини олиб ташлаш. Қайтади: олиб ташланган ходим ёки None."""
    current = Timekeeper.objects.select_related('employee').filter(department=department).first()
    if current is None:
        return None
    employee = current.employee
    current.delete()
    return employee
