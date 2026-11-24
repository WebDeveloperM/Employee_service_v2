"""«Табелчилар»: ҳар бир цех учун битта ходим ва унинг «Табелчи» роли.

Тайинлаш ва олиб ташлаш фақат шу ерда: Timekeeper ёзуви ва ходимнинг роли (UserProfile.role) ҳар доим бирга ўзгаради.
Ходимда ҳисоб (логин) бўлмаса, тайинлашда логин ва парол сўралади ва ҳисоб ходим формасидаги каби яратилади.
Администратор, HR ва суперфойдаланувчи табелчи бўла олмайди: тайинлаш уларнинг ролини пасайтириб қўймаслиги керак.
"""
from django.contrib.auth.models import User as AuthUser
from django.db import models, transaction

from .models import Department, Employee, ROLE_TIMEKEEPER, ROLE_USER, Timekeeper, UserProfile
from .serializers import create_login_user, is_privileged_account, login_problem, password_problems

CANDIDATES_LIMIT = 30


class TimekeeperError(Exception):
    """Тайинлаш мумкин эмас: матн фойдаланувчига кўрсатилади, field — хато қайси майдонга тегишли (ихтиёрий)."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


def employee_brief(employee):
    """Ходим ҳақида жадвал ва қидирув учун қисқа маълумот."""
    user = employee.user
    return {
        'id': employee.id,
        'slug': employee.slug,
        'full_name': str(employee),
        'tabel_number': employee.tabel_number,
        'position': employee.position,
        'section_name': employee.section.name if employee.section else '',
        'base_image_url': employee.base_image.url if employee.base_image else None,
        'login': (user.username if user is not None else '') or employee.login,
        'has_account': user is not None,
    }


def timekeepers_payload():
    """Барча цехлар (цех тартибида) ва уларнинг табелчиси (йўқ бўлса — None)."""
    assignments = {
        item.department_id: item
        for item in Timekeeper.objects.select_related('employee', 'employee__user', 'employee__section')
    }
    counts = dict(
        Employee.objects.filter(is_deleted=False, is_active=True, department__isnull=False)
        .values_list('department_id').annotate(total=models.Count('id'))
    )
    payload = []
    for department in Department.objects.order_by('sort_order', 'name'):
        assignment = assignments.get(department.id)
        payload.append({
            'department': {'id': department.id, 'name': department.name},
            'employee_count': counts.get(department.id, 0),
            'timekeeper': employee_brief(assignment.employee) if assignment is not None else None,
        })
    return payload


def candidates_for(department, search=''):
    """Цехнинг фаол ходимлари (исм, табель ёки лавозим бўйича қидирув): табелчи бўлиши мумкинлар ва ҳолати.

    Қайтади: (рўйхат, чекловга сиғмай қолганми).
    """
    queryset = Employee.objects.select_related('user', 'section', 'timekeeper_assignment__department').filter(
        department=department, is_deleted=False, is_active=True,
    ).order_by('last_name', 'first_name', 'surname', 'tabel_number')
    for word in (search or '').split():
        queryset = queryset.filter(
            models.Q(first_name__icontains=word) | models.Q(last_name__icontains=word) | models.Q(surname__icontains=word)
            | models.Q(tabel_number__icontains=word) | models.Q(position__icontains=word)
        )
    employees = list(queryset[:CANDIDATES_LIMIT + 1])
    truncated = len(employees) > CANDIDATES_LIMIT
    result = []
    for employee in employees[:CANDIDATES_LIMIT]:
        item = employee_brief(employee)
        assignment = getattr(employee, 'timekeeper_assignment', None)
        item['is_privileged'] = is_privileged_account(employee.user)
        item['timekeeper_of'] = assignment.department.name if assignment is not None else None
        result.append(item)
    return result, truncated


def demote(employee):
    """Табелчи ролини олиш: оддий «Фойдаланувчи»га қайтади (бошқа рол — администратор ва ҳ.к. — тегилмайди)."""
    profile = getattr(employee.user, 'profile', None) if employee.user_id else None
    if profile is not None and profile.role == ROLE_TIMEKEEPER:
        profile.role = ROLE_USER
        profile.save(update_fields=['role'])


def create_account(employee, login, password, password_confirm):
    """Ҳисоби йўқ ходимга логин ва парол билан ҳисоб (ходим формасидаги қоидалар билан бир хил)."""
    login = (login or '').strip()
    password = (password or '').strip()
    if not login:
        raise TimekeeperError('Ходимда ҳисоб йўқ: логинни киритинг.', 'login')
    problem = login_problem(login)
    if problem:
        raise TimekeeperError(problem, 'login')
    if Employee.objects.filter(login=login).exclude(pk=employee.pk).exists():
        raise TimekeeperError('Бундай логинли ходим аллақачон мавжуд.', 'login')
    if AuthUser.objects.filter(username=login).exists():
        raise TimekeeperError('Бундай логинли фойдаланувчи аллақачон мавжуд.', 'login')
    if not password:
        raise TimekeeperError('Парол киритинг.', 'password')
    if password != (password_confirm or '').strip():
        raise TimekeeperError('Пароллар мос келмайди.', 'password_confirm')
    problems = password_problems(password)
    if problems:
        raise TimekeeperError(problems if isinstance(problems, str) else ' '.join(problems), 'password')

    user = create_login_user(
        username=login, first_name=employee.first_name, last_name=employee.last_name, password=password, is_active=True,
    )
    UserProfile.objects.create(user=user, role=ROLE_TIMEKEEPER)
    employee.user = user
    employee.login = login
    employee.password_hash = user.password
    employee.save(update_fields=['user', 'login', 'password_hash', 'updated_at'])
    return user


@transaction.atomic
def assign_timekeeper(department, employee, *, login='', password='', password_confirm=''):
    """Цехга табелчи тайинлаш (олдингиси бўлса — алмашади). Қайтади: Timekeeper."""
    if employee.is_deleted or not employee.is_active:
        raise TimekeeperError('Фаол бўлмаган ходимни табелчи қилиб бўлмайди.', 'employee_id')
    if employee.department_id != department.id:
        raise TimekeeperError('Табелчи шу цехнинг ходими бўлиши керак.', 'employee_id')
    other = Timekeeper.objects.select_related('department').filter(employee=employee).exclude(department=department).first()
    if other is not None:
        raise TimekeeperError(f'Бу ходим аллақачон «{other.department.name}» цехининг табелчиси.', 'employee_id')

    user = employee.user
    if user is not None:
        if is_privileged_account(user):
            raise TimekeeperError('Администратор ёки HR менежерни табелчи қилиб бўлмайди.', 'employee_id')
        if not user.is_active:
            raise TimekeeperError('Ходимнинг ҳисоби ўчирилган: аввал уни «Фойдаланувчилар рухсатлари»да ёқинг.', 'employee_id')
        profile, created = UserProfile.objects.get_or_create(user=user, defaults={'role': ROLE_TIMEKEEPER})
        if not created and profile.role != ROLE_TIMEKEEPER:
            profile.role = ROLE_TIMEKEEPER
            profile.save(update_fields=['role'])
    else:
        create_account(employee, login, password, password_confirm)

    current = Timekeeper.objects.select_related('employee', 'employee__user').filter(department=department).first()
    if current is not None and current.employee_id == employee.id:
        return current
    if current is not None:
        demote(current.employee)
        current.delete()
    return Timekeeper.objects.create(department=department, employee=employee)


@transaction.atomic
def remove_timekeeper(department):
    """Цехнинг табелчисини олиб ташлаш (ҳисоб қолади, рол — оддий «Фойдаланувчи»). Қайтади: олиб ташланганми."""
    current = Timekeeper.objects.select_related('employee', 'employee__user').filter(department=department).first()
    if current is None:
        return False
    demote(current.employee)
    current.delete()
    return True
