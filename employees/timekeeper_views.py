"""API саҳифаси «Табелчилар» (администратор ва HR): цехлар рўйхати, табелчи тайинлаш ва олиб ташлаш, номзод қидириш."""
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Department, Employee
from .permissions import IsAdminOrHrRole
from .staff import is_leadership_department
from .timekeepers import (
    TimekeeperError, assign_timekeeper, candidates_for, employee_brief, remove_timekeeper, timekeepers_payload,
)

TIMEKEEPER_PERMISSIONS = [IsAuthenticated, IsAdminOrHrRole]


def error_response(error):
    return Response({'success': False, 'message': error.message, 'field': error.field}, status=status.HTTP_400_BAD_REQUEST)


class TimekeeperListTemplateView(APIView):
    permission_classes = TIMEKEEPER_PERMISSIONS

    def get(self, request, *args, **kwargs):
        return Response({'success': True, 'message': 'Табелчилар муваффақиятли олинди', 'data': timekeepers_payload()})


class TimekeeperCandidatesTemplateView(APIView):
    """Цехга табелчи қилиб тайинлаш мумкин ходимлар: фақат шу цехнинг (`department_id`) фаол ходимлари.

    Ҳар бир ходимда: ҳисоби борми, администратор/HR эканми, бошқа цехнинг табелчисими — шу ерда кўринади.
    «Раҳбарият» цехига табелчи тайинланмайди: рўйхат бўш ва `empty_reason` да сабаби.
    """

    permission_classes = TIMEKEEPER_PERMISSIONS

    def get(self, request, *args, **kwargs):
        department_id = request.query_params.get('department_id', '').strip()
        if not department_id.isdigit():
            return Response({'success': False, 'message': 'Цехни кўрсатинг (department_id).', 'field': 'department_id'},
                            status=status.HTTP_400_BAD_REQUEST)
        department = get_object_or_404(Department, pk=int(department_id))
        data = candidates_for(department, request.query_params.get('search', ''))
        empty_reason = ''
        if is_leadership_department(department):
            empty_reason = '«Раҳбарият» цехига табелчи тайинланмайди: унинг ходимлари табелчи бўла олмайди.'
        elif not data and not request.query_params.get('search', '').strip():
            empty_reason = 'Бу цехда фаол ходим йўқ.'
        return Response({
            'success': True, 'message': 'Ходимлар муваффақиятли олинди',
            'department': {'id': department.id, 'name': department.name}, 'data': data, 'empty_reason': empty_reason,
        })


class TimekeeperDetailTemplateView(APIView):
    """PUT — цехга табелчи тайинлаш (олдингиси алмашади), DELETE — олиб ташлаш."""

    permission_classes = TIMEKEEPER_PERMISSIONS

    def put(self, request, department_id, *args, **kwargs):
        department = get_object_or_404(Department, pk=department_id)
        employee_id = request.data.get('employee_id')
        if not str(employee_id or '').isdigit():
            return Response({'success': False, 'message': 'Ходимни танланг.', 'field': 'employee_id'}, status=status.HTTP_400_BAD_REQUEST)
        employee = Employee.objects.select_related('user', 'department', 'section').filter(pk=int(employee_id), is_deleted=False).first()
        if employee is None:
            return Response({'success': False, 'message': 'Ходим топилмади.', 'field': 'employee_id'}, status=status.HTTP_404_NOT_FOUND)
        try:
            assignment = assign_timekeeper(
                department, employee,
                login=request.data.get('login', ''),
                password=request.data.get('password', ''),
                password_confirm=request.data.get('password_confirm', ''),
            )
        except TimekeeperError as error:
            return error_response(error)
        return Response({
            'success': True,
            'message': f'«{department.name}» цехига табелчи тайинланди',
            'data': {'department': {'id': department.id, 'name': department.name}, 'timekeeper': employee_brief(assignment.employee)},
        })

    def delete(self, request, department_id, *args, **kwargs):
        department = get_object_or_404(Department, pk=department_id)
        if not remove_timekeeper(department):
            return Response({'success': False, 'message': 'Бу цехда табелчи тайинланмаган.'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, 'message': 'Табелчи олиб ташланди'})
