import logging

from requests import Response

from django_swagger_tester.configuration import settings
from django_swagger_tester.schema_validation.case.base import ResponseCaseTester, SchemaCaseTester
from django_swagger_tester.schema_validation.response.base import ResponseTester
from django_swagger_tester.utils import unpack_response

logger = logging.getLogger('django_swagger_tester')


def response_validation(response: Response, method: str, route: str, **kwargs) -> None:  # noqa: TYP001
    """
    Verifies that an OpenAPI schema definition matches an API response.

    It inspects the schema recursively, and verifies that the schema matches the structure of the response at every level.

    :param response: HTTP response
    :param method: HTTP method ('get', 'put', 'post', ...)
    :param route: Relative path of the endpoint being tested
    :raises: django_swagger_tester.exceptions.SwaggerDocumentationError or django_swagger_tester.exceptions.CaseError
    """
    data, status_code = unpack_response(response)
    response_schema = settings.LOADER_CLASS.get_response_schema(route=route, status_code=status_code, method=method)
    ResponseTester(response_schema=response_schema, response_data=data, **kwargs)
    ResponseCaseTester(response_data=data, **kwargs)
    SchemaCaseTester(schema=response_schema, **kwargs)
