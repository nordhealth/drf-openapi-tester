import json
import logging
from copy import deepcopy
from json import JSONDecodeError
from typing import Callable, Union

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse

from django_swagger_tester.configuration import settings
from django_swagger_tester.exceptions import SwaggerDocumentationError
from django_swagger_tester.schema_validation.openapi import list_types, read_type
from django_swagger_tester.schema_validation.schema_tester import SchemaTester
from django_swagger_tester.schema_validation.utils import resolve_path, get_endpoint_paths
from django_swagger_tester.testing import validate_response_schema

logger = logging.getLogger('django_swagger_tester')

"""
Note:

The middleware should have two modes:

- Default
- Strict

Default should log errors when one of the three validation types (response, uri param, request body) fail.
Strict should reject incoming requests when uri param og request body validation fails.
"""


class SwaggerValidationMiddleware(object):
    """
    # TODO: Verify or update before release

    This middleware performs three levels of validation:

    - Request body validation, with respect to the documented request body from the OpenAPI schema
    - URI parameter validation, with respect to the documented URI parameters in the OpenAPI schema
    - Response validation, with respect to the documented responses in the OpenAPI schema

    With the default settings, a failure in one of these levels of validation will log a message
    at the specied log level (the default is to log as an error),

    If strict validation is specified in the package settings, failures in request validation will
    return a 400-response, indicating what went wrong.
    """

    def __init__(self, get_response: Callable) -> None:
        """
        One-time configuration and initialization of the middleware.
        """
        self.endpoints = get_endpoint_paths()
        self.get_response = get_response

        # This logic cannot be moved to configuration.py because apps are not yet initialized when that is executed
        if not apps.is_installed('django_swagger_tester'):
            raise ImproperlyConfigured('django_swagger_tester must be listed in your installed apps')

    def __call__(self, request: HttpRequest) -> Union[HttpRequest, HttpResponse]:
        """
        Method is called for every incoming request and outgoing response.

        Validates

            - incoming request bodies,
            - incoming URI params, and
            - outgoing responses

        with respect to the application's OpenAPI schema.

        :param request: HttpRequest from Django
        :return: Passes on the request or response to the next middleware
        """
        path = request.path
        method = request.method.upper()
        api_request = False  # whether we should handle the request at all
        try:
            resolved_path = resolve_path(path)
        except ValueError:
            logger.debug('Skipping validation for %s request to %s', method, path)
        else:
            # Determine whether the request is being made to an API
            for route in self.endpoints:
                if resolved_path == route:
                    logger.debug('Request to %s is an API request', path)
                    api_request = True
                    break

        validate_request_body = api_request and request.body and settings.MIDDLEWARE.VALIDATE_REQUEST_BODY

        if validate_request_body:

            # Fetch the section of the OpenAPI schema related to the request body of this query
            request_body_schema = settings.LOADER_CLASS.get_request_body_schema_section(path, method)

            # Read the type of the request body from the schema
            request_body_type = read_type(request_body_schema)

            middleware_settings = settings.MIDDLEWARE

            # Verify that hte type is valid
            if request_body_type not in list_types():
                middleware_settings.LOGGER(
                    'Received unexpected type name, `%s`, for the request body of %s, %s',
                    request_body_type,
                    path,
                    method,
                )

            # Try to parse the incoming byte-data as the type the expected type
            handler = {
                'object': {'exception': JSONDecodeError, 'loader': json.loads},
                'array': {'exception': JSONDecodeError, 'loader': json.loads},
                'boolean': {'exception': JSONDecodeError, 'loader': json.loads},
                'string': {'exception': ValueError, 'loader': str},
                'file': {'exception': ValueError, 'loader': str},
                'integer': {'exception': ValueError, 'loader': int},
                'number': {'exception': ValueError, 'loader': float},
            }[request_body_type]

            try:
                # Parse request body as the correct type
                value = handler['loader'](request.body)  # type: ignore
            except handler['exception']:  # type: ignore
                # Handle parsing failure
                msg = f'Failed to parse request body as {request_body_type}'
                if middleware_settings.STRICT:
                    # Return 400 if strict
                    return HttpResponse(content=bytes(msg, 'utf-8'), status=400)
                else:
                    # Log otherwise
                    middleware_settings.LOGGER(msg)
            else:
                try:
                    # If parsing did not fail, run schema tester
                    SchemaTester(request_body_schema, value)
                except SwaggerDocumentationError as e:
                    # Handle bad request body error
                    if middleware_settings.STRICT:
                        # Return 400 if strict
                        example = settings.LOADER_CLASS.get_request_body_example(path, method)
                        msg = f'Request body is invalid. The request body should have the following format: {example}'
                        return HttpResponse(content=bytes(msg, 'utf-8'), status=400)
                    else:
                        # Log otherwise
                        middleware_settings.LOGGER(
                            'Bad request body passed for %s request to %s. Swagger error: \n\n%s', method, path, e
                        )

        # ^ Code above this line is executed before the view and later middleware
        response = self.get_response(request)

        validate_response = settings.MIDDLEWARE.VALIDATE_RESPONSE and api_request

        if validate_response:
            copied_response = deepcopy(response)
            copied_response.json = lambda: response.data
            try:
                validate_response_schema(response=copied_response, method=method, route=path)
            except SwaggerDocumentationError as e:
                settings.MIDDLEWARE.LOGGER(
                    'Incorrect response template returned for %s request to %s. Swagger error: \n\n%s', method, path, e
                )
            except IndexError as e:
                # TODO: Fix this section - handling indexerror is not robus enough
                logger.debug('Failed indexing schema')

        return response
