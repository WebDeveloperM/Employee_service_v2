import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .models import Department, Employee, Section, StaffPosition, UserProfile, ROLE_CHOICES, ROLE_USER
from .sections_tree import MAX_SECTION_DEPTH, SectionIndex
from .staff import collect_staff_stats, vacant_places

from django.contrib.auth.models import User as AuthUser


EMPLOYEE_DATE_INPUT_FORMATS = ['%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%d']
EMPLOYEE_DATE_OUTPUT_FORMAT = '%d/%m/%Y'


def normalize_uploaded_filename(file_name, max_stem_length=40):
    original_name = os.path.basename(file_name or '')
    stem, extension = os.path.splitext(original_name)
    safe_extension = extension[:10]
    safe_stem = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in stem).strip('-_') or 'upload'
    safe_stem = safe_stem[:max_stem_length]
    return f'{safe_stem}-{uuid4().hex[:8]}{safe_extension}'


class BlankableDateField(serializers.DateField):
    def to_internal_value(self, value):
        if value in ('', None):
            return None
        return super().to_internal_value(value)


class SanitizedImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if hasattr(data, 'name') and data.name:
            data.name = normalize_uploaded_filename(data.name)
        return super().to_internal_value(data)


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username')
    first_name = serializers.CharField(source='user.first_name', allow_blank=True, required=False)
    last_name = serializers.CharField(source='user.last_name', allow_blank=True, required=False)
    email = serializers.EmailField(source='user.email', allow_blank=True, required=False)
    is_active = serializers.BooleanField(source='user.is_active', required=False)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    has_face_image = serializers.SerializerMethodField()
    face_image_url = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'user_id',
            'username',
            'first_name',
            'last_name',
            'email',
            'role',
            'role_display',
            'is_active',
            'password',
            'face_image_url',
            'has_face_image',
        ]

    def validate_username(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Логинни киритиш мажбурий")
        queryset = User.objects.filter(username=value.strip())
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.user_id)
        if queryset.exists():
            raise serializers.ValidationError("Бундай логинли фойдаланувчи аллақачон мавжуд")
        return value.strip()

    def validate_password(self, value):
        if not self.instance and (not value or not value.strip()):
            raise serializers.ValidationError("Янги фойдаланувчи учун парол мажбурий")
        
        if value and len(value) < 6:
            raise serializers.ValidationError("Парол камида 6 та белгидан иборат бўлиши керак")
        
        if value and not any(c.isdigit() for c in value):
            raise serializers.ValidationError("Парол камида битта рақамни ўз ичига олиши керак")
        
        if value and not any(c.isalpha() for c in value):
            raise serializers.ValidationError("Парол камида битта ҳарфни ўз ичига олиши керак")
        
        return value

    def _validate_unique_profile_field(self, field_name, value):
        normalized = (value or '').strip()
        if not normalized:
            return ''

        queryset = UserProfile.objects.filter(**{field_name: normalized})
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError({field_name: 'Бу қиймат аллақачон бошқа фойдаланувчига боғланган.'})
        return normalized

    def validate(self, attrs):
        role = attrs.get('role', getattr(self.instance, 'role', ROLE_USER))
        has_existing_face = bool(getattr(self.instance, 'avatar_image', None))

        if role in {'admin', 'hr'} and not has_existing_face:
            raise serializers.ValidationError({'role': 'Администратор ва HR менежер роллари учун ходим карточкасида асосий тасвирни юклаш керак.'})

        return attrs

    def _clone_uploaded_image(self, uploaded_file, fallback_name):
        if uploaded_file in (None, serializers.empty):
            return None

        file_name = os.path.basename(getattr(uploaded_file, 'name', '') or fallback_name)
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        content = uploaded_file.read()
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        return ContentFile(content, name=file_name)

    def _sync_profile_face_image(self, profile, uploaded_file):
        if profile is None:
            return
        if uploaded_file is None:
            profile.face_image = None
            profile.save(update_fields=['face_image'])
            return

        profile.face_image = self._clone_uploaded_image(uploaded_file, 'profile-face.jpg')
        profile.save(update_fields=['face_image'])

    def create(self, validated_data):
        user_data = validated_data.pop('user', {})
        password = validated_data.pop('password', '')
        role = validated_data.get('role', ROLE_USER)

        user = User.objects.create_user(
            username=user_data.get('username', ''),
            first_name=user_data.get('first_name', ''),
            last_name=user_data.get('last_name', ''),
            email=user_data.get('email', ''),
            password=password or 'changeme123',
            is_active=user_data.get('is_active', True),
        )
        return UserProfile.objects.create(user=user, role=role)

    def update(self, instance, validated_data):
        user_data = validated_data.pop('user', {})
        password = validated_data.pop('password', '')
        next_role = validated_data.get('role', instance.role)
        employee = getattr(instance.user, 'employee', None)

        user = instance.user
        if 'username' in user_data:
            user.username = user_data['username']
        if 'first_name' in user_data:
            user.first_name = user_data['first_name']
        if 'last_name' in user_data:
            user.last_name = user_data['last_name']
        if 'email' in user_data:
            user.email = user_data['email']
        if 'is_active' in user_data:
            user.is_active = user_data['is_active']
        if password:
            user.set_password(password)
        user.save()

        if employee is not None:
            update_fields = []
            if employee.login != user.username:
                employee.login = user.username
                update_fields.append('login')
            if employee.is_active != user.is_active:
                employee.is_active = user.is_active
                update_fields.append('is_active')
            if password and employee.password_hash != user.password:
                employee.password_hash = user.password
                update_fields.append('password_hash')
            if update_fields:
                employee.save(update_fields=update_fields)

        instance.role = next_role
        instance.save()
        return instance

    def get_has_face_image(self, obj):
        return bool(obj.avatar_image)

    def get_face_image_url(self, obj):
        avatar_image = obj.avatar_image
        if not avatar_image:
            return None

        request = self.context.get('request')
        if request is None:
            return avatar_image.url
        return request.build_absolute_uri(avatar_image.url)


class DepartmentSerializer(serializers.ModelSerializer):
    # «Цех ID си» в форме — это sort_order: по нему упорядочены цеха. 0 (по умолчанию) — выбрать автоматически
    # (см. Department.save: число из названия «20-Цех» или следующий свободный номер).
    sort_order = serializers.IntegerField(
        required=False,
        min_value=0,
        max_value=2147483647,
        error_messages={
            'invalid': 'ID бутун сон бўлиши керак.',
            'min_value': 'ID манфий бўлмаслиги керак.',
            'max_value': 'ID жуда катта.',
        },
    )

    class Meta:
        model = Department
        fields = ['id', 'name', 'boss_full_name', 'sort_order']


class SectionSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        write_only=True,
        required=False,
        allow_null=True,
    )
    # Родительский отдел (NULL — верхний уровень). Читается и пишется как parent_id.
    parent_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(), source='parent', required=False, allow_null=True,
    )
    path = serializers.SerializerMethodField()
    depth = serializers.SerializerMethodField()
    children_count = serializers.SerializerMethodField()

    class Meta:
        model = Section
        fields = ['id', 'code', 'name', 'department', 'department_id', 'parent_id', 'path', 'depth', 'children_count']
        # Пустой список вместо автоматического: DRF 3.15 разворачивает двухполевое ограничение
        # (department, code) и делает code обязательным со значением по умолчанию '' — тогда PUT
        # без code затирал бы ID, а создание без ID не проходило бы. Правило «название уникально
        # в цехе» (unique_together) сохранено, но проверяется в validate(): стандартный
        # UniqueTogetherValidator отвечал по-английски («The fields department_id, name must make
        # a unique set.») и не говорил, какой именно отдел занимает это название.
        validators = []

    def _index(self):
        # Список передаёт готовый индекс в context; для одиночных ответов строим один раз.
        index = self.context.get('section_index')
        if index is None:
            index = self.context['section_index'] = SectionIndex()
        return index

    def save(self, **kwargs):
        instance = super().save(**kwargs)
        self.context.pop('section_index', None)   # индекс устарел: путь и глубина могли измениться
        return instance

    def get_path(self, obj):
        return self._index().path(obj.pk)

    def get_depth(self, obj):
        return self._index().depth(obj.pk)

    def get_children_count(self, obj):
        return self._index().children_count(obj.pk)

    def validate_code(self, value):
        return (value or '').strip()

    def _duplicate_name_message(self, existing, parent, *, creating):
        where = 'асосий бўлим' if existing.parent_id is None else self._index().path(existing.pk)
        message = f'Бу цехда «{existing.name}» номли бўлим аллақачон мавжуд ({where}). Бўлим номи цех бўйича ноёб бўлиши керак.'
        # Типик ҳолат: мавжуд асосий бўлимни ички бўлим қилмоқчи — янги нусха яратиш эмас, мавжудини кўчириш керак.
        if creating and parent is not None and existing.parent_id is None:
            message += f' Уни «{parent.name}» ичига кўчириш учун янгисини яратманг: мавжуд бўлимни таҳрирлаб, «Юқори бўлим»ни танланг.'
        return message

    def validate(self, attrs):
        # При правке цех, ID или родитель могут не прийти — берём их из самого отдела.
        instance = self.instance
        code = attrs.get('code', instance.code if instance is not None else '')
        department = attrs.get('department', instance.department if instance is not None else None)
        parent = attrs['parent'] if 'parent' in attrs else (instance.parent if instance is not None else None)

        # Новый отдел всегда создаётся в каком-то цеху (раньше это требовал UniqueTogetherValidator).
        if instance is None and department is None:
            raise serializers.ValidationError({'department_id': 'Цехни танланг.'})

        # Название уникально внутри цеха (и для вложенных отделов тоже: на это опирается импорт из Excel).
        name = attrs.get('name', instance.name if instance is not None else None)
        if name is not None and department is not None:
            same_name = Section.objects.filter(department=department, name=name)
            if instance is not None:
                same_name = same_name.exclude(pk=instance.pk)
            existing = same_name.first()
            if existing is not None:
                raise serializers.ValidationError({'name': self._duplicate_name_message(existing, parent, creating=instance is None)})

        # «ID отдела» уникален среди «соседей»: отделы верхнего уровня нумеруются внутри цеха,
        # вложенные — внутри своего родителя (у каждого родителя своя нумерация 1, 2, 3 …).
        if code and department is not None:
            siblings = Section.objects.filter(parent=parent) if parent is not None else Section.objects.filter(department=department, parent__isnull=True)
            duplicates = siblings.filter(code=code)
            if instance is not None:
                duplicates = duplicates.exclude(pk=instance.pk)
            if duplicates.exists():
                message = 'Бу юқори бўлимда шундай ID ли ички бўлим аллақачон мавжуд.' if parent is not None else 'Бу цехда шундай ID ли асосий бўлим аллақачон мавжуд.'
                raise serializers.ValidationError({'code': message})

        # Ички бўлимлар: ота шу цехда бўлиши, ўзи/ўз ичкиларига ўтказиб бўлмайди, чуқурлик чекланган.
        if parent is not None:
            index = self._index()
            if department is not None and parent.department_id != department.pk:
                raise serializers.ValidationError({'parent_id': 'Юқори бўлим танланган цехга тегишли эмас.'})
            if instance is not None:
                if parent.pk == instance.pk or parent.pk in index.descendants(instance.pk):
                    raise serializers.ValidationError({'parent_id': 'Бўлимни ўзига ёки ўзининг ички бўлимига бириктириб бўлмайди.'})
            below = index.height(instance.pk) + 1 if instance is not None else 1
            if index.depth(parent.pk) + below > MAX_SECTION_DEPTH - 1:   # 10 поғона = чуқурлик 0…9
                raise serializers.ValidationError({'parent_id': f'Бўлимлар {MAX_SECTION_DEPTH} поғонадан ортиқ ичма-ич жойлаша олмайди.'})

        # Ички бўлимлари бор бўлимни бошқа цехга ўтказсак, улар ота билан бирга қолмайди.
        if (instance is not None and department is not None and department.pk != instance.department_id
                and instance.children.exists()):
            raise serializers.ValidationError({'department_id': 'Ички бўлимлари бор бўлимни бошқа цехга ўтказиб бўлмайди.'})
        return attrs


class StaffPositionSerializer(serializers.ModelSerializer):
    # department/section задаются только при создании: должность не «переезжает» между единицами.
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), source='department', required=False, allow_null=True,
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.select_related('department'), source='section', required=False, allow_null=True,
    )
    # Дробные ставки допустимы (0,5 / 0,25 / 1,5): сотых долей достаточно, ответ — числом (не строкой), как раньше.
    staff_count = serializers.DecimalField(
        max_digits=9, decimal_places=2, min_value=Decimal('0.01'), required=False, coerce_to_string=False,
        error_messages={
            'invalid': 'Штатлар сонини сон билан киритинг (масалан, 1 ёки 0.5).',
            'min_value': 'Штатлар сони 0.01 дан кам бўлмаслиги керак.',
            'max_decimal_places': 'Вергулдан кейин энг кўпи билан 2 та рақам киритиш мумкин (масалан, 0.25).',
            'max_digits': 'Штатлар сони жуда катта.',
            'max_whole_digits': 'Штатлар сони жуда катта.',
        },
    )
    salary = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal('0'), required=False, allow_null=True,
    )
    occupied = serializers.SerializerMethodField()
    vacant = serializers.SerializerMethodField()

    class Meta:
        model = StaffPosition
        fields = ['id', 'department_id', 'section_id', 'name', 'staff_count', 'salary', 'occupied', 'vacant']
        # DRF 3.15 сам разворачивает условные UniqueConstraint модели и делает department_id
        # и section_id обязательными одновременно. Дубли проверяет validate() (без учёта регистра).
        validators = []

    def _occupied_for(self, obj):
        # Список передаёт готовую статистику в context; для одиночных ответов
        # (создание/правка) считаем один раз и кладём обратно в context.
        stats = self.context.get('staff_stats')
        if stats is None:
            stats = self.context['staff_stats'] = collect_staff_stats()
        return stats.occupied_for(obj.department_id, obj.section_id, obj.name)

    def get_occupied(self, obj):
        return self._occupied_for(obj)

    def get_vacant(self, obj):
        return vacant_places(obj.staff_count, self._occupied_for(obj))

    def validate_name(self, value):
        value = ' '.join((value or '').split())
        if not value:
            raise serializers.ValidationError('Лавозим номини киритинг.')
        return value

    def validate(self, attrs):
        if self.instance is not None:
            attrs.pop('department', None)
            attrs.pop('section', None)
            department = self.instance.department
            section = self.instance.section
        else:
            department = attrs.get('department')
            section = attrs.get('section')
            if section is not None:
                if department is not None and department.pk != section.department_id:
                    raise serializers.ValidationError('Танланган бўлим бошқа цехга тегишли.')
                department = section.department
                attrs['department'] = department
            if department is None:
                raise serializers.ValidationError('Цех ёки бўлимни танланг.')

        name = attrs.get('name', getattr(self.instance, 'name', ''))
        duplicates = StaffPosition.objects.filter(name__iexact=name)
        duplicates = duplicates.filter(section=section) if section is not None else duplicates.filter(department=department, section__isnull=True)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError({'name': 'Бу лавозим танланган бирликда аллақачон мавжуд.'})
        return attrs


class EmployeeSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)
    section = SectionSerializer(read_only=True)
    tabel_number = serializers.CharField(
        validators=[
            UniqueValidator(
                queryset=Employee.objects.all(),
                message='Бундай табель рақамли ходим аллақачон қўшилган.',
            )
        ]
    )
    date_of_birth = BlankableDateField(
        required=False,
        allow_null=True,
        input_formats=EMPLOYEE_DATE_INPUT_FORMATS,
        format=EMPLOYEE_DATE_OUTPUT_FORMAT,
    )
    surname = serializers.CharField(required=False, allow_blank=True)
    gender = serializers.ChoiceField(
        choices=Employee._meta.get_field('gender').choices,
        required=False,
        allow_blank=True,
    )
    phone_number_1 = serializers.CharField(required=False, allow_blank=True)
    phone_number_2 = serializers.CharField(required=False, allow_blank=True)
    clothe_size = serializers.CharField(required=False, allow_blank=True)
    date_of_employment = BlankableDateField(
        required=False,
        allow_null=True,
        input_formats=EMPLOYEE_DATE_INPUT_FORMATS,
        format=EMPLOYEE_DATE_OUTPUT_FORMAT,
    )
    date_of_change_position = BlankableDateField(
        required=False,
        allow_null=True,
        input_formats=EMPLOYEE_DATE_INPUT_FORMATS,
        format=EMPLOYEE_DATE_OUTPUT_FORMAT,
    )
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        source='department',
        write_only=True,
        required=False,
        allow_null=True,
    )
    section_id = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.all(),
        source='section',
        write_only=True,
        required=False,
        allow_null=True,
    )
    department_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    section_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    boss_full_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    full_name = serializers.SerializerMethodField()
    base_image_url = serializers.SerializerMethodField()
    base_image = SanitizedImageField(required=False, allow_null=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password_confirm = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES, default=ROLE_USER, write_only=True, required=False)

    class Meta:
        model = Employee
        fields = [
            'id',
            'source_system',
            'external_id',
            'first_name',
            'last_name',
            'surname',
            'full_name',
            'tabel_number',
            'gender',
            'date_of_birth',
            'special_clothing_size',
            'clothe_size',
            'shoe_size',
            'jacket_size',
            'tshirt_size',
            'phone_number_1',
            'phone_number_2',
            'login',
            'password',
            'password_confirm',
            'role',
            'base_image',
            'base_image_url',
            'department',
            'section',
            'department_id',
            'section_id',
            'department_name',
            'section_name',
            'boss_full_name',
            'position',
            'grade',
            'date_of_employment',
            'date_of_change_position',
            'slug',
            'is_active',
            'is_deleted',
            'requires_face_id_checkout',
            'metadata',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['slug', 'created_at', 'updated_at', 'password_hash']

    def validate(self, attrs):
        department = attrs.get('department', getattr(self.instance, 'department', None))
        section = attrs.get('section', getattr(self.instance, 'section', None))
        if department and section and section.department_id != department.id:
            raise serializers.ValidationError({'section_id': 'Бўлим танланган цехга тегишли эмас.'})

        password = attrs.get('password', '')
        password_confirm = attrs.get('password_confirm', '')
        if password or password_confirm:
            if password != password_confirm:
                raise serializers.ValidationError({'password_confirm': 'Пароллар мос келмайди.'})

        login = attrs.get('login', '').strip()
        if login:
            qs = Employee.objects.filter(login=login)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({'login': 'Бундай логинли ходим аллақачон мавжуд.'})
            user_qs = AuthUser.objects.filter(username=login)
            if self.instance and self.instance.user_id:
                user_qs = user_qs.exclude(pk=self.instance.user_id)
            if user_qs.exists():
                raise serializers.ValidationError({'login': 'Бундай логинли фойдаланувчи аллақачон мавжуд.'})

        return attrs

    def _clone_uploaded_image(self, uploaded_file, fallback_name):
        if uploaded_file in (None, serializers.empty):
            return None

        file_name = os.path.basename(getattr(uploaded_file, 'name', '') or fallback_name)
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        content = uploaded_file.read()
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        return ContentFile(content, name=file_name)

    def _sync_profile_face_image(self, profile, uploaded_file):
        if profile is None:
            return
        if uploaded_file is None:
            profile.face_image = None
            profile.save(update_fields=['face_image'])
            return

        profile.face_image = self._clone_uploaded_image(uploaded_file, 'profile-face.jpg')
        profile.save(update_fields=['face_image'])

    def create(self, validated_data):
        department_name = validated_data.pop('department_name', '').strip()
        section_name = validated_data.pop('section_name', '').strip()
        boss_full_name = validated_data.pop('boss_full_name', '').strip()
        password = validated_data.pop('password', '').strip()
        validated_data.pop('password_confirm', None)
        department, section = self._resolve_department_section(
            validated_data.get('department'),
            validated_data.get('section'),
            department_name,
            section_name,
            boss_full_name,
        )
        validated_data['department'] = department
        validated_data['section'] = section
        if password:
            validated_data['password_hash'] = make_password(password)

        login_value = validated_data.get('login', '').strip()
        role_value = validated_data.pop('role', ROLE_USER)
        if login_value:
            auth_user = AuthUser.objects.create_user(
                username=login_value,
                first_name=validated_data.get('first_name', ''),
                last_name=validated_data.get('last_name', ''),
                password=password or 'changeme123',
                is_active=validated_data.get('is_active', True),
            )
            UserProfile.objects.create(user=auth_user, role=role_value)
            validated_data['user'] = auth_user

        base_image = validated_data.get('base_image', serializers.empty)
        employee = super().create(validated_data)
        if base_image is not serializers.empty and employee.user_id:
            self._sync_profile_face_image(employee.user.profile, base_image)
        return employee

    def update(self, instance, validated_data):
        department_name = validated_data.pop('department_name', '').strip()
        section_name = validated_data.pop('section_name', '').strip()
        boss_full_name = validated_data.pop('boss_full_name', '').strip()
        password = validated_data.pop('password', '').strip()
        login_value = validated_data.get('login', instance.login).strip()
        is_active = validated_data.get('is_active', instance.is_active)
        validated_data.pop('password_confirm', None)
        department, section = self._resolve_department_section(
            validated_data.get('department', instance.department),
            validated_data.get('section', instance.section),
            department_name,
            section_name,
            boss_full_name,
        )
        validated_data['department'] = department
        validated_data['section'] = section
        linked_user = instance.user
        base_image = validated_data.get('base_image', serializers.empty)

        if login_value and linked_user is None:
            role_value = ROLE_USER
            if instance.user_id:
                role_value = instance.user.profile.role

            linked_user = AuthUser.objects.create_user(
                username=login_value,
                first_name=validated_data.get('first_name', instance.first_name),
                last_name=validated_data.get('last_name', instance.last_name),
                password=password or 'changeme123',
                is_active=is_active,
            )
            UserProfile.objects.create(user=linked_user, role=role_value)
            validated_data['user'] = linked_user
            if password:
                validated_data['password_hash'] = linked_user.password
        elif linked_user is not None:
            if login_value:
                linked_user.username = login_value
            linked_user.first_name = validated_data.get('first_name', instance.first_name)
            linked_user.last_name = validated_data.get('last_name', instance.last_name)
            linked_user.is_active = is_active
            if password:
                linked_user.set_password(password)
                validated_data['password_hash'] = linked_user.password
            linked_user.save()

        if password and 'password_hash' not in validated_data:
            validated_data['password_hash'] = make_password(password)
        employee = super().update(instance, validated_data)
        if base_image is not serializers.empty and employee.user_id:
            self._sync_profile_face_image(employee.user.profile, base_image)
        return employee

    def _resolve_department_section(self, department, section, department_name, section_name, boss_full_name):
        if department is None and department_name:
            department, _ = Department.objects.get_or_create(
                name=department_name,
                defaults={'boss_full_name': boss_full_name},
            )
            if boss_full_name and department.boss_full_name != boss_full_name:
                department.boss_full_name = boss_full_name
                department.save(update_fields=['boss_full_name'])

        if section is None and department is not None and section_name:
            section, _ = Section.objects.get_or_create(
                department=department,
                name=section_name,
            )

        return department, section

    def get_full_name(self, obj):
        return str(obj)

    def get_base_image_url(self, obj):
        if not obj.base_image:
            return None

        request = self.context.get('request')
        if request is None:
            return obj.base_image.url
        return request.build_absolute_uri(obj.base_image.url)


class EmployeeUpsertSerializer(EmployeeSerializer):
    class Meta(EmployeeSerializer.Meta):
        read_only_fields = ['slug', 'created_at', 'updated_at']