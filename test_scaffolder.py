"""TestScaffolder — Auto-generate test files from specifications."""

from pathlib import Path
from typing import List

from kodo.requirements_parser import Spec


class TestScaffolder:
    """Generate test files matching generated code structure."""

    def __init__(self, framework: str = "jest"):
        """
        Initialize test scaffolder.

        Args:
            framework: Test framework ("jest", "pytest", "mocha")
        """
        self.framework = framework

    def generate_api_tests(self, spec: Spec, output_dir: Path) -> str:
        """
        Generate API tests.

        Args:
            spec: Project specification
            output_dir: Output directory for tests

        Returns:
            Generated test file content
        """
        if self.framework == "jest":
            return self._generate_jest_tests(spec)
        elif self.framework == "pytest":
            return self._generate_pytest_tests(spec)
        elif self.framework == "mocha":
            return self._generate_mocha_tests(spec)
        else:
            raise ValueError(f"Unsupported test framework: {self.framework}")

    def _generate_jest_tests(self, spec: Spec) -> str:
        """Generate Jest tests for Express APIs."""
        test_code = """import request from 'supertest';
import app from '../src/index';

describe('API Tests', () => {
  describe('Health Check', () => {
    it('should return 200 on /health', async () => {
      const response = await request(app).get('/health');
      expect(response.status).toBe(200);
      expect(response.body).toHaveProperty('message');
    });
  });

"""

        # Add tests for auth routes
        if spec.auth:
            test_code += """  describe('Authentication', () => {
    it('should register a new user', async () => {
      const response = await request(app)
        .post('/auth/register')
        .send({ email: 'test@example.com', password: 'password123' });
      expect(response.status).toBe(200);
    });

    it('should login a user', async () => {
      const response = await request(app)
        .post('/auth/login')
        .send({ email: 'test@example.com', password: 'password123' });
      expect(response.status).toBe(200);
    });
  });

"""

        # Add tests for features
        for feature in spec.features:
            resource = feature.name.lower().replace(" ", "_")
            test_code += f"""  describe('{feature.name}', () => {{
    it('should list all {resource}', async () => {{
      const response = await request(app)
        .get('/{resource}')
        .set('Authorization', 'Bearer token');
      expect(response.status).toBe(200);
    }});

    it('should create a new {resource}', async () => {{
      const response = await request(app)
        .post('/{resource}')
        .set('Authorization', 'Bearer token')
        .send({{ name: 'Test' }});
      expect(response.status).toBe(200);
    }});
  }});

"""

        test_code += "});\n"
        return test_code

    def _generate_pytest_tests(self, spec: Spec) -> str:
        """Generate Pytest tests for FastAPI/Django APIs."""
        test_code = """import pytest
from app import app

@pytest.fixture
def client():
    \"\"\"Create test client.\"\"\"
    return app.test_client()

class TestAPI:
    \"\"\"API tests.\"\"\"

    def test_health_check(self, client):
        \"\"\"Test health endpoint.\"\"\"
        response = client.get('/health')
        assert response.status_code == 200

"""

        if spec.auth:
            test_code += """    def test_register_user(self, client):
        \"\"\"Test user registration.\"\"\"
        response = client.post(
            '/auth/register',
            json={'email': 'test@example.com', 'password': 'password123'}
        )
        assert response.status_code == 200

    def test_login_user(self, client):
        \"\"\"Test user login.\"\"\"
        response = client.post(
            '/auth/login',
            json={'email': 'test@example.com', 'password': 'password123'}
        )
        assert response.status_code == 200

"""

        for feature in spec.features:
            resource = feature.name.lower().replace(" ", "_")
            test_code += f"""    def test_list_{resource}(self, client):
        \"\"\"Test listing {resource}.\"\"\"
        response = client.get('/{resource}')
        assert response.status_code == 200

"""

        return test_code

    def _generate_mocha_tests(self, spec: Spec) -> str:
        """Generate Mocha tests."""
        test_code = """const request = require('supertest');
const app = require('../src/index');

describe('API Tests', () => {
  describe('Health Check', () => {
    it('should return 200 on /health', (done) => {
      request(app)
        .get('/health')
        .expect(200, done);
    });
  });

"""

        if spec.auth:
            test_code += """  describe('Authentication', () => {
    it('should register a user', (done) => {
      request(app)
        .post('/auth/register')
        .send({ email: 'test@example.com', password: 'password' })
        .expect(200, done);
    });
  });

"""

        test_code += "});\n"
        return test_code

    def generate_unit_tests(self, output_dir: Path) -> str:
        """
        Generate unit test template.

        Args:
            output_dir: Output directory

        Returns:
            Test template content
        """
        if self.framework == "jest":
            template = """describe('Unit Tests', () => {
  it('should pass', () => {
    expect(true).toBe(true);
  });
});
"""
        elif self.framework == "pytest":
            template = """def test_example():
    \"\"\"Example unit test.\"\"\"
    assert True
"""
        else:
            template = """describe('Unit Tests', () => {
  it('should pass', () => {
    assert.ok(true);
  });
});
"""
        return template


def generate_tests(spec: Spec, output_dir: Path, framework: str = "jest") -> None:
    """
    Convenience function to generate test files.

    Args:
        spec: Project specification
        output_dir: Output directory for tests
        framework: Test framework
    """
    scaffolder = TestScaffolder(framework)
    
    # Create tests directory if needed
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate API tests
    api_tests = scaffolder.generate_api_tests(spec, output_dir)
    test_file = "api.test.ts" if framework == "jest" else "test_api.py"
    (output_dir / test_file).write_text(api_tests)
    
    # Generate unit test template
    unit_tests = scaffolder.generate_unit_tests(output_dir)
    unit_file = "unit.test.ts" if framework == "jest" else "test_unit.py"
    (output_dir / unit_file).write_text(unit_tests)
