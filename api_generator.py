"""ApiGenerator — Auto-generate API endpoints from specifications."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from kodo.requirements_parser import Spec


@dataclass
class ApiRoute:
    """Single API route specification."""
    path: str
    method: str  # "GET", "POST", "PUT", "DELETE", "PATCH"
    name: str
    description: str
    authentication_required: bool = True
    request_body: Optional[Dict] = None
    response_schema: Optional[Dict] = None


class ApiGenerator:
    """Generate API endpoint code from specifications."""

    def __init__(self, framework: str = "express"):
        """
        Initialize API generator.

        Args:
            framework: Backend framework ("express", "fastapi", "django")
        """
        self.framework = framework

    def generate_routes_from_spec(self, spec: Spec) -> List[ApiRoute]:
        """
        Generate API routes from specification.

        Args:
            spec: Project specification

        Returns:
            List of API routes
        """
        routes = []

        # Always add health check
        routes.append(
            ApiRoute(
                path="/health",
                method="GET",
                name="health_check",
                description="Health check endpoint",
                authentication_required=False,
            )
        )

        # Add auth routes if needed
        if spec.auth:
            routes.extend(self._generate_auth_routes(spec))

        # Add CRUD routes for features
        for feature in spec.features:
            routes.extend(self._generate_feature_routes(feature))

        return routes

    def _generate_auth_routes(self, spec: Spec) -> List[ApiRoute]:
        """Generate authentication-related routes."""
        routes = []

        if spec.auth.auth_type == "jwt":
            routes.extend([
                ApiRoute(
                    path="/auth/register",
                    method="POST",
                    name="register_user",
                    description="Register a new user",
                    authentication_required=False,
                    request_body={
                        "email": "string",
                        "password": "string",
                    },
                    response_schema={
                        "id": "string",
                        "email": "string",
                        "token": "string",
                    },
                ),
                ApiRoute(
                    path="/auth/login",
                    method="POST",
                    name="login_user",
                    description="Login user",
                    authentication_required=False,
                    request_body={
                        "email": "string",
                        "password": "string",
                    },
                    response_schema={
                        "token": "string",
                        "user": {"id": "string", "email": "string"},
                    },
                ),
                ApiRoute(
                    path="/auth/profile",
                    method="GET",
                    name="get_profile",
                    description="Get current user profile",
                    authentication_required=True,
                ),
            ])

        if spec.auth.providers:
            for provider in spec.auth.providers:
                routes.append(
                    ApiRoute(
                        path=f"/auth/{provider}/callback",
                        method="GET",
                        name=f"{provider}_callback",
                        description=f"OAuth callback for {provider}",
                        authentication_required=False,
                    )
                )

        return routes

    def _generate_feature_routes(self, feature) -> List[ApiRoute]:
        """Generate routes for a specific feature."""
        routes = []

        # Convert feature name to route
        route_name = feature.name.lower().replace(" ", "-")

        # Only generate routes if feature requires API
        if not feature.requires_api:
            return routes

        # Standard CRUD routes
        resource = route_name
        routes.extend([
            ApiRoute(
                path=f"/{resource}",
                method="GET",
                name=f"list_{resource}",
                description=f"List all {resource}",
                authentication_required=True,
            ),
            ApiRoute(
                path=f"/{resource}",
                method="POST",
                name=f"create_{resource}",
                description=f"Create a new {resource}",
                authentication_required=True,
                request_body={"name": "string"},
            ),
            ApiRoute(
                path=f"/{resource}/{{id}}",
                method="GET",
                name=f"get_{resource}",
                description=f"Get a specific {resource}",
                authentication_required=True,
            ),
            ApiRoute(
                path=f"/{resource}/{{id}}",
                method="PUT",
                name=f"update_{resource}",
                description=f"Update a {resource}",
                authentication_required=True,
                request_body={"name": "string"},
            ),
            ApiRoute(
                path=f"/{resource}/{{id}}",
                method="DELETE",
                name=f"delete_{resource}",
                description=f"Delete a {resource}",
                authentication_required=True,
            ),
        ])

        return routes

    def generate_code(self, routes: List[ApiRoute], output_path: Path) -> str:
        """
        Generate code for API routes.

        Args:
            routes: List of routes to generate
            output_path: Path where code will be written

        Returns:
            Generated code as string
        """
        if self.framework == "express":
            return self._generate_express_code(routes, output_path)
        elif self.framework == "fastapi":
            return self._generate_fastapi_code(routes, output_path)
        elif self.framework == "django":
            return self._generate_django_code(routes, output_path)
        else:
            raise ValueError(f"Unsupported framework: {self.framework}")

    def _generate_express_code(self, routes: List[ApiRoute], output_path: Path) -> str:
        """Generate Express.js code."""
        code = """\
import express, { Router, Request, Response } from 'express';

const router = Router();

// Middleware for authentication (placeholder)
const authenticate = (req: Request, res: Response, next: Function) => {
  // TODO: Implement JWT verification
  next();
};

// Error handler
const asyncHandler = (fn: Function) => (req: Request, res: Response, next: Function) =>
  Promise.resolve(fn(req, res, next)).catch(next);

"""

        for route in routes:
            code += self._generate_express_route(route)

        code += """\
export default router;
"""

        output_path.write_text(code)
        return code

    def _generate_express_route(self, route: ApiRoute) -> str:
        """Generate a single Express route."""
        method = route.method.lower()
        auth = f"authenticate, " if route.authentication_required else ""
        path = route.path

        code = f"""\
// {route.description}
router.{method}(
  '{path}',
  {auth}asyncHandler(async (req: Request, res: Response) => {{
    try {{
      // TODO: Implement {route.name}
      res.json({{"message": "Not implemented yet"}});
    }} catch (error) {{
      res.status(500).json({{"error": error.message}});
    }}
  }})
);

"""
        return code

    def _generate_fastapi_code(self, routes: List[ApiRoute], output_path: Path) -> str:
        """Generate FastAPI code."""
        code = """\
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from datetime import datetime

router = APIRouter()

# Dependency for authentication (placeholder)
async def verify_token(token: str = Depends(oauth2_scheme)):
    # TODO: Implement JWT verification
    return token

"""

        for route in routes:
            code += self._generate_fastapi_route(route)

        output_path.write_text(code)
        return code

    def _generate_fastapi_route(self, route: ApiRoute) -> str:
        """Generate a single FastAPI route."""
        method = route.method.lower()
        path = route.path.replace("{", "{").replace("}", "}")
        auth = ", current_user: str = Depends(verify_token)" if route.authentication_required else ""

        code = f"""\
@router.{method}("{path}")
async def {route.name}({auth}) -> dict:
    \"\"\"
    {route.description}
    \"\"\"
    # TODO: Implement {route.name}
    return {{"message": "Not implemented yet"}}

"""
        return code

    def _generate_django_code(self, routes: List[ApiRoute], output_path: Path) -> str:
        """Generate Django code."""
        code = """\
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json

# Helper decorators
def require_authentication(view_func):
    \"\"\"Decorator to require authentication.\"\"\"
    def wrapper(request, *args, **kwargs):
        # TODO: Implement JWT verification
        return view_func(request, *args, **kwargs)
    return wrapper

"""

        for route in routes:
            code += self._generate_django_view(route)

        output_path.write_text(code)
        return code

    def _generate_django_view(self, route: ApiRoute) -> str:
        """Generate a single Django view."""
        methods = route.method.upper()
        auth = "@require_authentication\n" if route.authentication_required else ""

        code = f"""\
{auth}@require_http_methods(["{methods}"])
def {route.name}(request):
    \"\"\"
    {route.description}
    \"\"\"
    # TODO: Implement {route.name}
    return JsonResponse({{"message": "Not implemented yet"}})

"""
        return code

    def generate_schema_json(self, routes: List[ApiRoute], output_path: Path) -> str:
        """
        Generate OpenAPI/JSON schema for the API.

        Args:
            routes: List of routes
            output_path: Path where schema will be written

        Returns:
            Generated schema as JSON string
        """
        schema = {
            "openapi": "3.0.0",
            "info": {
                "title": "Generated API",
                "version": "1.0.0",
            },
            "paths": {},
        }

        for route in routes:
            if route.path not in schema["paths"]:
                schema["paths"][route.path] = {}

            schema["paths"][route.path][route.method.lower()] = {
                "summary": route.name,
                "description": route.description,
                "tags": ["api"],
                "responses": {
                    "200": {
                        "description": "Success",
                    },
                    "401": {
                        "description": "Unauthorized",
                    },
                    "500": {
                        "description": "Server error",
                    },
                },
            }

            if route.request_body:
                schema["paths"][route.path][route.method.lower()]["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": route.request_body,
                        }
                    },
                }

        schema_json = json.dumps(schema, indent=2)
        output_path.write_text(schema_json)
        return schema_json


def generate_api(spec: Spec, output_dir: Path, framework: str = "express") -> None:
    """
    Convenience function to generate API from specification.

    Args:
        spec: Project specification
        output_dir: Output directory for generated code
        framework: Backend framework to target
    """
    generator = ApiGenerator(framework)
    routes = generator.generate_routes_from_spec(spec)
    generator.generate_code(routes, output_dir / "routes.ts")
    generator.generate_schema_json(routes, output_dir / "openapi.json")
