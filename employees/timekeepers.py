"""Табелчилар: ҳар бир цех учун битта ходим.

Табелчини ES да эмас, БОШҚА ТИЗИМ тайинлайди (API: timekeeper_api.py, ҳужжат: API_TIMEKEEPER.md); ES да тайинлаш саҳифаси йўқ.
Тайинлаш ва олиб ташлаш фақат шу ерда: Timekeeper ёзуви ва (ходимда ES ҳисоби бўлса) унинг роли — UserProfile.role —
ҳар доим бирга ўзгаради.

Қоидалар
  * Табелчи — тайинланган цехнинг ўз фаол ходими. «Раҳбарият» цехи ходими табелчи бўла олмайди.
  * Ходим фақат битта цехнинг табелчиси бўла олади; цехнинг табелчиси битта (янги тайинлаш эскисини алмаштиради).
  * Администратор, HR ва суперфойдаланувчи табелчи бўла олмайди: тайинлаш уларнинг ролини пасайтириб қўймаслиги керак.
  * ES ҳисоби МАЖБУРИЙ ЭМАС: ҳисоби йўқ табелчи бошқа тизим орқали ишлайди. ES ҳисоби бор бўлса, унга «Табелчи» роли
    берилади ва у ES веб-саҳифасидаги «Цех ходимлари»дан ҳам фойдалана олади. ES да ҳисоб ЯРАТИЛМАЙДИ (логин/парол
    тизимлар ўртасида юрмасин).
"""
from typing import NamedTuple

from django.db import models, transaction
from django.utils import timezone

from .models import Department, Employee, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile
from .serializers import is_privileged_account
from .staff import is_leadership_department, staff_tree_order


class AssignmentResult(NamedTuple):
    assignment: Timekeeper
    replaced: Employee | None   # алмашган эски табелчи (ходим)
    changed: bool               # False — шу ходим аллақачон шу цехнинг табелчиси эди


# Тайинлаш хатоларининг кодлари (HTTP 409). Ҳар бири API_TIMEKEEPER.md да тавсифланган (тест текширади).
ASSIGNMENT_ERROR_CODES = (
    'employee_inactive', 'leadership_department', 'already_timekeeper_elsewhere', 'employee_not_in_department', 'privileged_account',
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
        Timekeeper.objects.select_related('department', 'employee', 'employee__user', 'employee__user__profile')
        .filter(employee__tabel_number=tabel_number, employee__is_deleted=False, employee__is_active=True)
        .first()
    )


def timekeeper_brief(assignment):
    """Тайинлов ҳақида маълумот (API жавоби учун)."""
    employee = assignment.employee
    user = employee.user
    return {
        'employee_id': employee.id,
        'tabel_number': employee.tabel_number,
        'full_name': str(employee),
        'position': employee.position,
        'is_active': bool(employee.is_active and not employee.is_deleted),   # False бўлса, у номидан иш қилиб бўлмайди
        'has_es_account': user is not None,
        'assigned_at': timezone.localtime(assignment.created_at).isoformat(),
    }


def assignments_payload(*, assigned=None, tabel_number=''):
    """Барча цехлар (штат дарахтидаги тартибда) ва уларнинг табелчиси (йўқ бўлса — None).

    assigned: True — фақат табелчиси борлари, False — фақат йўқлари; tabel_number — шу ходимнинг цехи.
    """
    assignments = {
        item.department_id: item
        for item in Timekeeper.objects.select_related('employee', 'employee__user')
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


def demote(employee):
    """Табелчи ролини олиш: оддий «Фойдаланувчи»га қайтади (бошқа рол — администратор ва ҳ.к. — тегилмайди)."""
    profile = getattr(employee.user, 'profile', None) if employee.user_id else None
    if profile is not None and profile.role == ROLE_TIMEKEEPER:
        profile.role = ROLE_USER
        profile.save(update_fields=['role'])


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

    user = employee.user
    if user is not None:
        if is_privileged_account(user):
            raise TimekeeperError('Администратор ёки HR менежерни табелчи қилиб бўлмайди.', 'privileged_account')
        profile, created = UserProfile.objects.get_or_create(user=user, defaults={'role': ROLE_TIMEKEEPER})
        if not created and profile.role != ROLE_TIMEKEEPER:
            profile.role = ROLE_TIMEKEEPER
            profile.save(update_fields=['role'])

    current = Timekeeper.objects.select_related('employee', 'employee__user').filter(department=department).first()
    if current is not None and current.employee_id == employee.id:
        return AssignmentResult(current, None, False)
    replaced = None
    if current is not None:
        replaced = current.employee
        demote(replaced)
        current.delete()
    return AssignmentResult(Timekeeper.objects.create(department=department, employee=employee), replaced, True)


@transaction.atomic
def remove_timekeeper(department):
    """Цехнинг табелчисини олиб ташлаш (ҳисоб қолади, рол — оддий «Фойдаланувчи»). Қайтади: олиб ташланган ходим ёки None."""
    current = Timekeeper.objects.select_related('employee', 'employee__user').filter(department=department).first()
    if current is None:
        return None
    employee = current.employee
    demote(employee)
    current.delete()
    return employee
