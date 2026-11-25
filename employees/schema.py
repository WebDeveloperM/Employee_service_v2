from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ServiceApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'employees.authentication.ServiceApiKeyAuthentication'
    name = 'EmployeeServiceApiKeyHeader'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-Employee-Service-Key',
            'description': 'API key for cross-project integration with employee_service.',
        }


class TimekeeperTokenAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'employees.timekeeper_api.TimekeeperTokenAuthentication'
    name = 'TimekeeperToken'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'Табелчи токени: «Authorization: Token <токен>». Токен POST /api/v1/timekeeper/token/ дан олинади.',
        }
