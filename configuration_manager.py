"""ConfigurationManager — Unified configuration system for generated projects."""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from kodo.requirements_parser import Spec


@dataclass
class ConfigValue:
    """Single configuration value."""
    key: str
    value: Any
    required: bool = True
    sensitive: bool = False
    description: str = ""
    default: Optional[Any] = None


class ConfigurationManager:
    """Manage centralized project configuration."""

    # Sensitive keys that should not be logged
    SENSITIVE_KEYS = {
        "password", "secret", "token", "key", "api_key", "apikey",
        "auth", "credential", "jwt", "oauth", "private"
    }

    def __init__(self, project_name: str):
        """
        Initialize configuration manager.

        Args:
            project_name: Name of the project
        """
        self.project_name = project_name
        self.configs: Dict[str, Dict[str, ConfigValue]] = {}
        self._initialize_default_configs()

    def _initialize_default_configs(self) -> None:
        """Initialize default configuration structure."""
        self.configs = {
            "app": {},
            "database": {},
            "auth": {},
            "server": {},
            "features": {},
        }

    def add_config(
        self,
        section: str,
        key: str,
        value: Any,
        required: bool = True,
        sensitive: bool = False,
        description: str = "",
        default: Optional[Any] = None,
    ) -> None:
        """
        Add a configuration value.

        Args:
            section: Configuration section
            key: Configuration key
            value: Configuration value
            required: Whether the config is required
            sensitive: Whether the value is sensitive (password, token, etc)
            description: Human description
            default: Default value if not provided
        """
        if section not in self.configs:
            self.configs[section] = {}

        # Auto-detect sensitive keys
        if self._is_sensitive_key(key):
            sensitive = True

        self.configs[section][key] = ConfigValue(
            key=key,
            value=value,
            required=required,
            sensitive=sensitive,
            description=description,
            default=default,
        )

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if key should be treated as sensitive."""
        key_lower = key.lower()
        return any(sensitive_key in key_lower for sensitive_key in self.SENSITIVE_KEYS)

    def generate_env_file(self, output_path: Path, environment: str = "development") -> str:
        """
        Generate .env file.

        Args:
            output_path: Path to write .env file
            environment: Environment (development, staging, production)

        Returns:
            Generated .env content
        """
        env_content = f"# Auto-generated configuration for {environment}\n"
        env_content += f"# Environment: {environment}\n"
        env_content += f"# Project: {self.project_name}\n\n"

        env_content += f"NODE_ENV={environment}\n"
        env_content += f"APP_ENV={environment}\n\n"

        # Generate config values
        for section, configs in self.configs.items():
            if configs:
                env_content += f"# {section.upper()} Configuration\n"

                for key, config in configs.items():
                    if config.sensitive:
                        env_content += f"{key.upper()}={config.default or ''}\n"
                    else:
                        env_content += f"{key.upper()}={config.value}\n"

                env_content += "\n"

        output_path.write_text(env_content)
        return env_content

    def generate_env_example(self, output_path: Path) -> str:
        """
        Generate .env.example file without sensitive values.

        Args:
            output_path: Path to write .env.example

        Returns:
            Generated .env.example content
        """
        example_content = "# Configuration template - copy to .env and fill in values\n\n"

        for section, configs in self.configs.items():
            if configs:
                example_content += f"# {section.upper()} Configuration\n"

                for key, config in configs.items():
                    if config.description:
                        example_content += f"# {config.description}\n"

                    if config.sensitive:
                        example_content += f"# {key.upper()}=<your_{key.lower()}_here>\n"
                    else:
                        example_content += f"{key.upper()}={config.default or config.value}\n"

                example_content += "\n"

        output_path.write_text(example_content)
        return example_content

    def generate_config_json(self, output_path: Path) -> str:
        """
        Generate config.json for structured config.

        Args:
            output_path: Path to write config.json

        Returns:
            Generated config.json content
        """
        config_dict = {}

        for section, configs in self.configs.items():
            config_dict[section] = {}

            for key, config in configs.items():
                config_dict[section][key] = {
                    "value": config.value if not config.sensitive else None,
                    "required": config.required,
                    "sensitive": config.sensitive,
                    "description": config.description,
                    "default": config.default,
                }

        config_json = json.dumps(config_dict, indent=2)
        output_path.write_text(config_json)
        return config_json

    def generate_config_ts(self, output_path: Path) -> str:
        """
        Generate config.ts for TypeScript projects.

        Args:
            output_path: Path to write config.ts

        Returns:
            Generated config.ts content
        """
        ts_code = """import dotenv from 'dotenv';

dotenv.config();

interface Config {
  app: {
    name: string;
    env: string;
    port: number;
  };
  database?: {
    url: string;
  };
  auth?: {
    jwt_secret: string;
  };
  [key: string]: any;
}

const config: Config = {
  app: {
    name: process.env.APP_NAME || 'Application',
    env: process.env.NODE_ENV || 'development',
    port: parseInt(process.env.PORT || '3000'),
  },
};

// Database config
if (process.env.DATABASE_URL) {
  config.database = {
    url: process.env.DATABASE_URL,
  };
}

// Auth config
if (process.env.JWT_SECRET) {
  config.auth = {
    jwt_secret: process.env.JWT_SECRET,
  };
}

export default config;
"""
        output_path.write_text(ts_code)
        return ts_code

    def generate_config_py(self, output_path: Path) -> str:
        """
        Generate config.py for Python projects.

        Args:
            output_path: Path to write config.py

        Returns:
            Generated config.py content
        """
        py_code = """import os
from typing import Dict, Any

class Config:
    \"\"\"Base configuration.\"\"\"
    # Application
    APP_NAME = os.getenv('APP_NAME', 'Application')
    ENV = os.getenv('APP_ENV', 'development')
    DEBUG = ENV == 'development'
    
    # Server
    HOST = os.getenv('HOST', 'localhost')
    PORT = int(os.getenv('PORT', 3000))
    
    # Database
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    
    # Auth
    JWT_SECRET = os.getenv('JWT_SECRET', 'change-me')
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION = 86400  # 24 hours


class DevelopmentConfig(Config):
    \"\"\"Development configuration.\"\"\"
    DEBUG = True


class ProductionConfig(Config):
    \"\"\"Production configuration.\"\"\"
    DEBUG = False


class TestingConfig(Config):
    \"\"\"Testing configuration.\"\"\"
    TESTING = True
    DATABASE_URL = 'sqlite:///:memory:'


# Config selector
CONFIG: Dict[str, type] = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}

config = CONFIG.get(Config.ENV, DevelopmentConfig)
"""
        output_path.write_text(py_code)
        return py_code

    def validate_config(self) -> List[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        for section, configs in self.configs.items():
            for key, config in configs.items():
                if config.required and config.value is None:
                    if config.default is None:
                        errors.append(f"{section}.{key} is required but not configured")

        return errors

    def load_from_env(self) -> None:
        """Load configuration from environment variables."""
        for section, configs in self.configs.items():
            for key, config in configs.items():
                env_key = key.upper()
                env_value = os.getenv(env_key, config.default)

                if env_value is not None:
                    self.configs[section][key].value = env_value

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert configuration to dictionary (excludes sensitive values).

        Returns:
            Configuration as dictionary
        """
        result = {}

        for section, configs in self.configs.items():
            result[section] = {}

            for key, config in configs.items():
                if config.sensitive:
                    result[section][key] = "***SENSITIVE***"
                else:
                    result[section][key] = config.value

        return result

    def to_json(self, include_sensitive: bool = False, indent: int = 2) -> str:
        """
        Convert configuration to JSON string.

        Args:
            include_sensitive: Whether to include sensitive values
            indent: JSON indent level

        Returns:
            Configuration as JSON string
        """
        config_dict = {}

        for section, configs in self.configs.items():
            config_dict[section] = {}

            for key, config in configs.items():
                if config.sensitive and not include_sensitive:
                    config_dict[section][key] = "***SENSITIVE***"
                else:
                    config_dict[section][key] = config.value

        return json.dumps(config_dict, indent=indent)


def generate_project_config(spec: Spec, output_dir: Path) -> ConfigurationManager:
    """
    Generate configuration from specification.

    Args:
        spec: Project specification
        output_dir: Output directory for config files

    Returns:
        ConfigurationManager instance
    """
    config = ConfigurationManager(spec.project_name)

    # App configuration
    config.add_config("app", "name", spec.project_name, description="Application name")
    config.add_config("app", "port", 3000, description="Server port")
    config.add_config("app", "debug", False, description="Debug mode")

    # Database configuration
    if spec.database:
        config.add_config(
            "database",
            "url",
            "",
            required=True,
            sensitive=True,
            description=f"{spec.database.db_type.upper()} connection URL",
        )

    # Auth configuration
    if spec.auth:
        config.add_config(
            "auth",
            "jwt_secret",
            "change-me-in-production",
            required=True,
            sensitive=True,
            description="JWT secret key",
        )

        if spec.auth.providers:
            for provider in spec.auth.providers:
                config.add_config(
                    "auth",
                    f"{provider}_client_id",
                    "",
                    required=False,
                    sensitive=False,
                    description=f"{provider.upper()} OAuth client ID",
                )
                config.add_config(
                    "auth",
                    f"{provider}_client_secret",
                    "",
                    required=False,
                    sensitive=True,
                    description=f"{provider.upper()} OAuth client secret",
                )

    # Feature-specific configuration
    for feature in spec.features:
        feature_section = feature.name.lower().replace(" ", "_")
        config.add_config(
            "features",
            feature_section,
            True,
            description=f"Enable {feature.name}",
        )

    # Generate output files
    output_dir.mkdir(parents=True, exist_ok=True)
    config.generate_env_file(output_dir / ".env")
    config.generate_env_example(output_dir / ".env.example")
    config.generate_config_json(output_dir / "config.json")

    # Generate language-specific configs
    if spec.backend_framework == "express":
        config.generate_config_ts(output_dir / "config.ts")
    elif spec.backend_framework in ["fastapi", "django"]:
        config.generate_config_py(output_dir / "config.py")

    return config
