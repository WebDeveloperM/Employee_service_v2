from django.contrib import admin

from .models import BnpzIdClient, Department, Employee, EmployeeCertificate, Section, StaffPosition, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'role', 'has_face_image')
    list_filter = ('role', 'user__employee__tabel_number')
    search_fields = (
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__employee__first_name',
        'user__employee__last_name',
        'user__employee__surname',
        'user__employee__tabel_number',
    )
    fieldsets = (
        (None, {'fields': ('user', 'role')}),
    )

    def has_face_image(self, obj):
        return bool(obj.avatar_image)
    has_face_image.boolean = True
    has_face_image.short_description = 'Face ID'


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'boss_full_name')
    search_fields = ('name', 'boss_full_name')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'name', 'department', 'parent')
    search_fields = ('code', 'name', 'department__name')
    list_filter = ('department',)
    raw_id_fields = ('parent',)


@admin.register(StaffPosition)
class StaffPositionAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'department', 'section', 'staff_count', 'salary')
    search_fields = ('name', 'department__name', 'section__name')
    list_filter = ('department',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BnpzIdClient)
class BnpzIdClientAdmin(admin.ModelAdmin):
    list_display = ('client_id', 'name', 'is_active', 'has_access_check', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('client_id', 'name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('client_id', 'name', 'client_secret', 'is_active')}),
        ('Redirect URIs', {'fields': ('allowed_redirects',)}),
        ('Проверка доступа', {'fields': ('access_check_url',), 'description': 'Оставьте пустым, чтобы разрешить доступ всем аутентифицированным пользователям'}),
        ('Даты', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    def has_access_check(self, obj):
        return bool(obj.access_check_url)
    has_access_check.boolean = True
    has_access_check.short_description = 'Access Check'


@admin.register(EmployeeCertificate)
class EmployeeCertificateAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'serial_number', 'issued_at', 'expires_at', 'revoked', 'issued_by_username')
    list_filter = ('revoked',)
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__tabel_number', 'serial_number', 'fingerprint_sha256')
    readonly_fields = ('serial_number', 'fingerprint_sha256', 'issued_at', 'expires_at', 'issued_by_username')
    actions = ['revoke_selected']

    def revoke_selected(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(revoked=False).update(revoked=True, revoked_at=timezone.now())
        self.message_user(request, f'Отозвано ключей: {updated}')
    revoke_selected.short_description = 'Отозвать выбранные ключи'


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source_system',
        'external_id',
        'tabel_number',
        'last_name',
        'first_name',
        'department',
        'section',
        'requires_face_id_checkout',
        'is_active',
        'updated_at',
    )
    search_fields = ('external_id', 'tabel_number', 'first_name', 'last_name', 'surname', 'slug')
    list_filter = ('tabel_number',)
    readonly_fields = ('slug', 'created_at', 'updated_at') 