"""RequirementsParser — Parse natural language goals into structured specs."""

import json
import re
from dataclasses import dataclass, asdict
from typing import Optional, List


@dataclass
class AuthConfig:
    """Authentication requirements."""
    auth_type: str  # "none", "jwt", "oauth2", "session"
    providers: List[str] = None  # ["google", "github", "email"]
    session_store: str = "memory"  # "memory", "redis", "database"


@dataclass
class DatabaseConfig:
    """Database configuration."""
    db_type: str  # "postgresql", "mongodb", "mysql", "sqlite"
    needs_migrations: bool = True
    needs_orm: bool = True
    orm_type: str = "prisma"  # "prisma", "sqlalchemy", "typeorm", "mongoose"


@dataclass
class Feature:
    """Single feature specification."""
    name: str
    description: str
    priority: str = "medium"  # "low", "medium", "high", "critical"
    requires_api: bool = False
    requires_ui: bool = False


@dataclass
class TechStackChoice:
    """Technology choice."""
    category: str  # "language", "framework", "database", "auth", "deploy"
    choice: str


@dataclass
class Spec:
    """Fully structured requirements specification."""
    project_name: str
    description: str
    features: List[Feature]
    tech_stack: List[TechStackChoice]
    database: Optional[DatabaseConfig]
    auth: Optional[AuthConfig]
    frontend_framework: Optional[str]  # "react", "vue", "svelte", "next", "nuxt"
    backend_framework: Optional[str]  # "express", "fastapi", "django", "rails"
    deployment_target: Optional[str]  # "aws", "heroku", "docker", "vercel"
    estimated_effort_hours: int = 0

    def to_dict(self):
        """Convert to dictionary for serialization."""
        return {
            "project_name": self.project_name,
            "description": self.description,
            "features": [asdict(f) for f in self.features],
            "tech_stack": [asdict(t) for t in self.tech_stack],
            "database": asdict(self.database) if self.database else None,
            "auth": asdict(self.auth) if self.auth else None,
            "frontend_framework": self.frontend_framework,
            "backend_framework": self.backend_framework,
            "deployment_target": self.deployment_target,
            "estimated_effort_hours": self.estimated_effort_hours,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


class RequirementsParser:
    """Parse natural language goals into structured specs using pattern matching and NLP."""

    def __init__(self):
        """Initialize parser with keyword patterns."""
        self.frontend_keywords = {
            "react": ["react", "next", "nextjs", "next.js"],
            "vue": ["vue", "nuxt"],
            "svelte": ["svelte"],
            "angular": ["angular"],
            "vanilla": ["vanilla", "plain javascript", "html/css"],
        }

        self.backend_keywords = {
            "express": ["express", "node", "nodejs", "javascript", "ts"],
            "fastapi": ["fastapi", "python", "py"],
            "django": ["django", "python"],
            "rails": ["rails", "ruby", "rb"],
            "flask": ["flask", "python"],
        }

        self.db_keywords = {
            "postgresql": ["postgres", "postgresql", "pg", "psql"],
            "mongodb": ["mongo", "mongodb", "nosql"],
            "mysql": ["mysql", "mariadb"],
            "sqlite": ["sqlite", "local"],
        }

        self.auth_keywords = {
            "jwt": ["jwt", "json web token"],
            "oauth2": ["oauth", "oauth2", "google login", "github login"],
            "session": ["session", "cookie", "login"],
            "none": ["no auth", "public", "unauthenticated"],
        }

    def parse(self, goal: str, project_name: Optional[str] = None) -> Spec:
        """
        Parse a natural language goal into a structured specification.

        Args:
            goal: Natural language description of the project
            project_name: Optional project name (extracted from goal if not provided)

        Returns:
            Spec: Structured specification
        """
        goal_lower = goal.lower()

        # Extract project name
        if not project_name:
            project_name = self._extract_project_name(goal)

        # Extract tech stack choices
        frontend = self._detect_frontend(goal_lower)
        backend = self._detect_backend(goal_lower)
        database = self._detect_database(goal_lower)
        auth = self._detect_auth(goal_lower)
        deployment = self._detect_deployment(goal_lower)

        # Extract features
        features = self._extract_features(goal)

        # Build tech stack
        tech_stack = []
        if frontend:
            tech_stack.append(TechStackChoice("frontend", frontend))
        if backend:
            tech_stack.append(TechStackChoice("backend", backend))
        if database:
            tech_stack.append(TechStackChoice("database", database.db_type))
        if auth:
            tech_stack.append(TechStackChoice("auth", auth.auth_type))
        if deployment:
            tech_stack.append(TechStackChoice("deployment", deployment))

        # Estimate effort
        effort = self._estimate_effort(features, frontend, backend, database)

        return Spec(
            project_name=project_name,
            description=goal,
            features=features,
            tech_stack=tech_stack,
            database=database,
            auth=auth,
            frontend_framework=frontend,
            backend_framework=backend,
            deployment_target=deployment,
            estimated_effort_hours=effort,
        )

    def _extract_project_name(self, goal: str) -> str:
        """Extract project name from goal (first sentence or quoted string)."""
        # Look for quoted name
        match = re.search(r'["\']([A-Z][A-Za-z0-9\s]+)["\']', goal)
        if match:
            return match.group(1)

        # Use first word after common patterns
        patterns = [
            r"(?:build|create|implement|develop|make)\s+(?:a|an)\s+([A-Z][A-Za-z0-9\s]+?)(?:\s+(?:app|application|platform|tool|service))?[.!?]",
            r"^([A-Z][A-Za-z0-9\s]+?):\s+",
        ]
        for pattern in patterns:
            match = re.search(pattern, goal)
            if match:
                return match.group(1).strip()

        # Fallback: use first capitalized word
        words = goal.split()
        for word in words:
            if word[0].isupper():
                return word.rstrip(".:,;")

        return "MyApp"

    def _detect_frontend(self, goal: str) -> Optional[str]:
        """Detect frontend framework from goal text."""
        for framework, keywords in self.frontend_keywords.items():
            if any(kw in goal for kw in keywords):
                return framework
        # Default: assume React if frontend is needed
        if any(kw in goal for kw in ["web", "ui", "interface", "dashboard"]):
            return "react"
        return None

    def _detect_backend(self, goal: str) -> Optional[str]:
        """Detect backend framework from goal text."""
        for framework, keywords in self.backend_keywords.items():
            if any(kw in goal for kw in keywords):
                return framework
        # Default: assume Express if backend/API is mentioned
        if any(kw in goal for kw in ["api", "backend", "server", "rest"]):
            return "express"
        return None

    def _detect_database(self, goal: str) -> Optional[DatabaseConfig]:
        """Detect database type and configuration."""
        db_type = None
        for db, keywords in self.db_keywords.items():
            if any(kw in goal for kw in keywords):
                db_type = db
                break

        if not db_type:
            # Default: PostgreSQL if database is mentioned
            if any(kw in goal for kw in ["database", "db", "data", "store", "persistent"]):
                db_type = "postgresql"

        if db_type:
            # Detect ORM/migration needs
            needs_migrations = any(
                kw in goal for kw in ["migration", "schema", "version", "evolve"]
            )
            needs_orm = any(kw in goal for kw in ["orm", "model", "query"])

            # Select ORM based on backend
            orm_type = "prisma"  # default
            if "python" in goal.lower():
                orm_type = "sqlalchemy"
            elif "rails" in goal.lower():
                orm_type = "activerecord"
            elif "mongo" in goal.lower():
                orm_type = "mongoose"

            return DatabaseConfig(
                db_type=db_type,
                needs_migrations=needs_migrations,
                needs_orm=needs_orm,
                orm_type=orm_type,
            )

        return None

    def _detect_auth(self, goal: str) -> Optional[AuthConfig]:
        """Detect authentication requirements."""
        auth_type = None
        for auth, keywords in self.auth_keywords.items():
            if any(kw in goal for kw in keywords):
                auth_type = auth
                break

        if not auth_type:
            # Default: JWT if auth is mentioned
            if any(
                kw in goal
                for kw in ["auth", "login", "user", "account", "secure", "permission"]
            ):
                auth_type = "jwt"

        if auth_type and auth_type != "none":
            # Detect OAuth providers
            providers = []
            if "google" in goal.lower():
                providers.append("google")
            if "github" in goal.lower():
                providers.append("github")
            if "facebook" in goal.lower():
                providers.append("facebook")

            return AuthConfig(
                auth_type=auth_type,
                providers=providers if providers else None,
                session_store="redis" if "redis" in goal.lower() else "memory",
            )

        return None

    def _detect_deployment(self, goal: str) -> Optional[str]:
        """Detect deployment target."""
        deployment_keywords = {
            "aws": ["aws", "amazon"],
            "heroku": ["heroku"],
            "vercel": ["vercel"],
            "docker": ["docker", "container"],
            "kubernetes": ["kubernetes", "k8s"],
        }

        for target, keywords in deployment_keywords.items():
            if any(kw in goal.lower() for kw in keywords):
                return target

        # Default to Docker if deployment mentioned
        if "deploy" in goal.lower():
            return "docker"

        return None

    def _extract_features(self, goal: str) -> List[Feature]:
        """Extract features from goal text."""
        features = []

        # Common feature patterns
        feature_patterns = [
            (r"(?:users?|accounts?|authentication|auth)", "User Authentication"),
            (r"dashboard", "Dashboard"),
            (r"(?:real-time|realtime)", "Real-time Updates"),
            (r"(?:email|notifications?)", "Email Notifications"),
            (r"(?:search|full-text|full text)", "Search"),
            (r"(?:api|rest|graphql)", "API"),
            (r"(?:file (?:upload|storage)|s3)", "File Upload/Storage"),
            (r"(?:admin|management)", "Admin Panel"),
            (r"(?:export|report)", "Reporting/Export"),
            (r"(?:analytics|metrics)", "Analytics"),
            (r"(?:payment|stripe|checkout)", "Payment Processing"),
        ]

        for pattern, feature_name in feature_patterns:
            if re.search(pattern, goal.lower()):
                # Determine priority
                priority = "medium"
                if any(
                    kw in goal.lower()
                    for kw in ["must", "critical", "essential", "core"]
                ):
                    priority = "critical"
                elif any(kw in goal.lower() for kw in ["nice to have", "optional", "future"]):
                    priority = "low"

                features.append(
                    Feature(
                        name=feature_name,
                        description=f"Implement {feature_name.lower()}",
                        priority=priority,
                        requires_api=True,
                        requires_ui=feature_name != "API",
                    )
                )

        # If no features found, create a generic one
        if not features:
            features.append(
                Feature(
                    name="Core Functionality",
                    description="Implement core application functionality",
                    priority="critical",
                    requires_api=True,
                )
            )

        return features

    def _estimate_effort(
        self, features: List[Feature], frontend: Optional[str], backend: Optional[str],
        database: Optional[DatabaseConfig]
    ) -> int:
        """Estimate effort in hours."""
        effort = 8  # Base effort

        # Add for each feature
        effort += len(features) * 4

        # Add for frontend
        if frontend:
            effort += 12

        # Add for backend
        if backend:
            effort += 12

        # Add for database
        if database:
            effort += 8
            if database.needs_migrations:
                effort += 4
            if database.needs_orm:
                effort += 4

        return effort


def parse_goal(goal: str) -> Spec:
    """Convenience function to parse a goal."""
    parser = RequirementsParser()
    return parser.parse(goal)
