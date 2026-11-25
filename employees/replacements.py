"""Ўринбосар: ходим вақтинча ишда бўлмаганда унинг ўрнига ишлайдиган, шу цехдаги ходим.

Ходим формасида «Ҳолати» «Фаол»дан бошқа қийматга ўзгартирилса, ўринбосар танлаш ойнаси очилади
(employee_detail.html). Танлов ходимнинг ўзи сақлангандагина ёзилади (EmployeeSerializer, `replacement_employee_id`).
"""
from django.db import transaction
from django.db.models import Q

from .models import Employee, EmployeeReplacement, WORK_STATUS_OVER_STAFF

CANDIDATE_LIMIT = 500   # цехда бундан кўп ходим бўлса, рўйхат кесилади ва «truncated» белгиланади


class ReplacementError(Exception):
    """Ўринбосарни тайинлаб бўлмаслигининг сабаби (фойдаланувчига кўрсатиладиган матн)."""


def substitute_candidates(department_id, *, exclude_pk=None):
    """Ўринбосар бўла оладиганлар: шу цехнинг фаол ва ҳозир ишдаги ходимлари («св.шт.» ҳам ишлаяпти).
    Таътил, сафар, касаллик ва «Фаол эмас» ходимлар — ўзи йўқ, шунинг учун ўринбосар бўла олмайди."""
    employees = Employee.objects.filter(department_id=department_id, is_deleted=False, is_active=True).filter(
        Q(work_status__isnull=True) | Q(work_status='') | Q(work_status=WORK_STATUS_OVER_STAFF),
    )
    if exclude_pk is not None:
        employees = employees.exclude(pk=exclude_pk)
    return employees.select_related('section')


def resolve_substitute(substitute_id, *, absent, department):
    """Танланган ўринбосарни текширади ва ходимни қайтаради. `absent` — ўрни бўшайдиган ходим (бор ёзув),
    `department` — унинг (сақлангандан кейинги) цехи."""
    if department is None:
        raise ReplacementError('Ўринбосар тайинлаш учун аввал ходимнинг цехини танланг.')
    substitute = Employee.objects.filter(pk=substitute_id, is_deleted=False).first()
    if substitute is None:
        raise ReplacementError('Ўринбосар ходим топилмади.')
    if absent.pk is not None and substitute.pk == absent.pk:
        raise ReplacementError('Ходим ўзининг ўринбосари бўла олмайди.')
    if substitute.department_id != department.pk:
        raise ReplacementError('Ўринбосар ходим билан бир цехдан бўлиши керак.')
    if not substitute.is_active or substitute.work_status not in (None, '', WORK_STATUS_OVER_STAFF):
        raise ReplacementError('Ўринбосар ҳозир ишда бўлган фаол ходим бўлиши керак.')
    return substitute


@transaction.atomic
def set_replacement(employee, substitute, assigned_by=None):
    record, _ = EmployeeReplacement.objects.update_or_create(
        employee=employee, defaults={'substitute': substitute, 'assigned_by': assigned_by},
    )
    return record


def clear_replacement(employee):
    EmployeeReplacement.objects.filter(employee=employee).delete()
