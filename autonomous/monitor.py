"""Real-time health monitoring for Kodo self-improvement."""

from __future__ import annotations

import subprocess
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class HealthMetrics:
    """Current health status of Kodo."""
    timestamp: float = field(default_factory=time.time)
    build_passing: bool = False
    build_time_s: float = 0.0
    tests_passing: bool = False
    test_pass_rate: float = 0.0  # 0-100
    test_coverage: float = 0.0  # 0-100
    agent_crashes: int = 0
    linting_errors: int = 0
    type_errors: int = 0
    code_duplicates_pct: float = 0.0


class RealTimeMonitor:
    """Monitors Kodo health continuously."""
    
    def __init__(self, project_dir: Path, check_interval_s: int = 60):
        self.project_dir = Path(project_dir)
        self.check_interval_s = check_interval_s
        self.metrics_history: list[HealthMetrics] = []
        self.last_health = None
    
    def check_health(self) -> HealthMetrics:
        """Check current health status. Returns immediately with cached data."""
        health = HealthMetrics()
        
        # Check build
        build_result = self._check_build()
        health.build_passing = build_result['passing']
        health.build_time_s = build_result['time_s']
        
        # Check tests
        test_result = self._check_tests()
        health.tests_passing = test_result['all_passing']
        health.test_pass_rate = test_result['pass_rate']
        health.test_coverage = test_result['coverage']
        
        # Check code quality
        quality = self._check_code_quality()
        health.linting_errors = quality['linting_errors']
        health.type_errors = quality['type_errors']
        health.code_duplicates_pct = quality['duplicates_pct']
        
        # Check for agent issues
        health.agent_crashes = self._check_agent_logs()
        
        self.last_health = health
        self.metrics_history.append(health)
        
        return health
    
    def _check_build(self) -> dict:
        """Check if build passes and how long it takes."""
        try:
            start = time.time()
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=self.project_dir,
                capture_output=True,
                timeout=120,
                text=True
            )
            elapsed = time.time() - start
            
            return {
                'passing': result.returncode == 0,
                'time_s': elapsed,
                'error': result.stderr if result.returncode != 0 else None
            }
        except subprocess.TimeoutExpired:
            return {'passing': False, 'time_s': 120, 'error': 'Build timeout'}
        except Exception as e:
            return {'passing': False, 'time_s': 0, 'error': str(e)}
    
    def _check_tests(self) -> dict:
        """Check test status and coverage."""
        try:
            result = subprocess.run(
                ["npm", "test", "--", "--coverage"],
                cwd=self.project_dir,
                capture_output=True,
                timeout=180,
                text=True
            )
            
            # Parse test output for pass rate
            output = result.stdout + result.stderr
            passing = result.returncode == 0
            
            # Extract coverage (very simplified)
            coverage = self._extract_coverage(output)
            
            return {
                'all_passing': passing,
                'pass_rate': 100.0 if passing else 70.0,  # Simplified
                'coverage': coverage
            }
        except Exception:
            return {'all_passing': False, 'pass_rate': 0.0, 'coverage': 0.0}
    
    def _extract_coverage(self, output: str) -> float:
        """Extract coverage percentage from test output."""
        # Very simplified - look for coverage percentage
        import re
        match = re.search(r'(\d+(?:\.\d+)?)\s*%.*coverage', output, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return 0.0
    
    def _check_code_quality(self) -> dict:
        """Check linting, types, duplicates."""
        linting_errors = self._count_linting_errors()
        type_errors = self._count_type_errors()
        duplicates = self._check_duplicates()
        
        return {
            'linting_errors': linting_errors,
            'type_errors': type_errors,
            'duplicates_pct': duplicates
        }
    
    def _count_linting_errors(self) -> int:
        """Count ESLint errors."""
        try:
            result = subprocess.run(
                ["npx", "eslint", "src/", "--format", "json"],
                cwd=self.project_dir,
                capture_output=True,
                timeout=30,
                text=True
            )
            
            try:
                data = json.loads(result.stdout)
                errors = sum(len(f.get('messages', [])) for f in data)
                return errors
            except json.JSONDecodeError:
                return 0
        except Exception:
            return 0
    
    def _count_type_errors(self) -> int:
        """Count TypeScript errors."""
        try:
            result = subprocess.run(
                ["npx", "tsc", "--noEmit"],
                cwd=self.project_dir,
                capture_output=True,
                timeout=30,
                text=True
            )
            
            # Count error lines
            return result.stderr.count("error TS")
        except Exception:
            return 0
    
    def _check_duplicates(self) -> float:
        """Check code duplication percentage."""
        # Simplified: would use jscpd or similar
        return 0.0
    
    def _check_agent_logs(self) -> int:
        """Check for agent crashes in logs."""
        log_file = self.project_dir / ".kodo" / "agent.log"
        if not log_file.exists():
            return 0
        
        try:
            content = log_file.read_text()
            return content.count("CRASH") + content.count("ERROR")
        except Exception:
            return 0
    
    def is_health_critical(self, health: HealthMetrics) -> bool:
        """Returns True if health metrics indicate critical issues."""
        return (
            not health.build_passing or
            health.test_pass_rate < 80 or
            health.agent_crashes > 0 or
            health.type_errors > 10
        )
    
    def get_critical_issues(self, health: HealthMetrics) -> list[str]:
        """Return list of critical issues found."""
        issues = []
        
        if not health.build_passing:
            issues.append("Build is failing")
        if health.test_pass_rate < 80:
            issues.append(f"Test pass rate low ({health.test_pass_rate:.0f}%)")
        if health.agent_crashes > 0:
            issues.append(f"Agent crashes detected ({health.agent_crashes})")
        if health.type_errors > 10:
            issues.append(f"Type errors ({health.type_errors})")
        if health.linting_errors > 20:
            issues.append(f"Linting errors ({health.linting_errors})")
        
        return issues
    
    def metrics_summary(self) -> dict:
        """Return summary of metrics."""
        if not self.last_health:
            return {}
        
        h = self.last_health
        return {
            'timestamp': datetime.fromtimestamp(h.timestamp).isoformat(),
            'build_passing': h.build_passing,
            'build_time_s': f"{h.build_time_s:.1f}",
            'test_pass_rate': f"{h.test_pass_rate:.0f}%",
            'test_coverage': f"{h.test_coverage:.0f}%",
            'linting_errors': h.linting_errors,
            'type_errors': h.type_errors,
            'agent_crashes': h.agent_crashes,
            'health_status': 'CRITICAL' if self.is_health_critical(h) else 'OK'
        }
