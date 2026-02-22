"""Continuous autonomous self-improvement loop for Kodo."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from kodo.autonomous.monitor import RealTimeMonitor, HealthMetrics
from kodo.autonomous.executor import AutoImprovementExecutor, Improvement
from kodo import log

# Ensure time is available in run_sync method
__all__ = ["ContinuousImprovementSystem", "create_system"]


class ContinuousImprovementSystem:
    """Main system that runs Kodo's autonomous self-improvement loop."""
    
    def __init__(self, project_dir: Path, worker_agent=None):
        self.project_dir = Path(project_dir)
        self.worker_agent = worker_agent
        
        # Core components
        self.monitor = RealTimeMonitor(project_dir, check_interval_s=60)
        self.executor = AutoImprovementExecutor(project_dir, worker_agent)
        
        # State
        self.improvement_queue: list[Improvement] = []
        self.metrics_history: list[HealthMetrics] = []
        self.system_started = time.time()
        self.total_improvements = 0
        self.running = False
    
    async def run(self):
        """Main loop: run forever improving Kodo."""
        self.running = True
        log.tprint("🚀 Starting Kodo Continuous Self-Improvement System")
        
        # Spawn background tasks
        tasks = [
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._analyze_loop()),
            asyncio.create_task(self._execute_loop()),
            asyncio.create_task(self._learn_loop()),
            asyncio.create_task(self._report_loop()),
        ]
        
        # Keep running until told to stop
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            log.tprint("⏹️  Shutting down...")
            self.running = False
    
    def run_sync(self):
        """Synchronous run loop (simpler, for daemon)."""
        import time
        
        self.running = True
        log.tprint("🚀 Starting Kodo Continuous Self-Improvement System (sync mode)")
        log.tprint(f"   Project: {self.project_dir}")
        log.tprint("")
        
        cycle = 0
        
        try:
            while self.running:
                cycle += 1
                
                # Every cycle: analyze for improvements
                improvements = self._analyze_codebase()
                for improvement in improvements:
                    if self._is_safe_to_implement(improvement):
                        self.improvement_queue.append(improvement)
                        log.tprint(f"📝 Queued: {improvement.title}")
                
                # Execute next improvement
                if self.improvement_queue:
                    metrics_before = self.monitor.metrics_summary()
                    result = self.executor.execute_next(metrics_before)
                    
                    if result:
                        self.total_improvements += 1
                        
                        if result.success:
                            log.tprint(f"✅ MERGED: {result.improvement.title}")
                        else:
                            log.tprint(f"❌ FAILED: {result.improvement.title}")
                
                # Report every 10 cycles
                if cycle % 10 == 0:
                    uptime = time.time() - self.system_started
                    summary = self.executor.execution_summary()
                    
                    log.tprint(f"\n📊 Status (cycle {cycle}, uptime {uptime/60:.1f}m)")
                    log.tprint(f"   Improvements: {self.total_improvements}")
                    log.tprint(f"   Queue: {len(self.improvement_queue)}")
                    log.tprint(f"   Success rate: {summary['success_rate']:.0f}%\n")
                
                # Sleep before next cycle
                time.sleep(30)
        
        except KeyboardInterrupt:
            log.tprint("\n⏹️  Shutting down...")
            self.running = False
    
    async def _monitor_loop(self):
        """Monitor health continuously (every 60s)."""
        while self.running:
            try:
                health = self.monitor.check_health()
                self.metrics_history.append(health)
                
                # If critical issues found, queue urgent fixes
                if self.monitor.is_health_critical(health):
                    issues = self.monitor.get_critical_issues(health)
                    for issue in issues:
                        log.tprint(f"🚨 CRITICAL: {issue}")
                        self._queue_urgent_fix(issue)
                
                await asyncio.sleep(60)  # Check every minute
            
            except Exception as e:
                log.tprint(f"❌ Monitor error: {e}")
                await asyncio.sleep(60)
    
    async def _analyze_loop(self):
        """Analyze codebase for improvements (every 30 min)."""
        while self.running:
            try:
                improvements = self._analyze_codebase()
                
                for improvement in improvements:
                    # Only queue if safe
                    if self._is_safe_to_implement(improvement):
                        self.improvement_queue.append(improvement)
                        log.tprint(f"📝 Queued: {improvement.title}")
                
                await asyncio.sleep(1800)  # Every 30 minutes
            
            except Exception as e:
                log.tprint(f"❌ Analysis error: {e}")
                await asyncio.sleep(1800)
    
    async def _execute_loop(self):
        """Execute improvements continuously (every 5 sec check)."""
        while self.running:
            try:
                if self.improvement_queue:
                    # Get current metrics as baseline
                    metrics_before = self.monitor.metrics_summary()
                    
                    # Execute next improvement
                    result = self.executor.execute_next(metrics_before)
                    
                    if result:
                        self.total_improvements += 1
                        
                        if result.success:
                            log.tprint(f"✅ MERGED: {result.improvement.title} ({result.execution_time_s:.0f}s)")
                        else:
                            log.tprint(f"❌ FAILED: {result.improvement.title} ({result.error})")
                
                await asyncio.sleep(5)  # Check every 5 seconds
            
            except Exception as e:
                log.tprint(f"❌ Execution error: {e}")
                await asyncio.sleep(5)
    
    async def _learn_loop(self):
        """Learn what works best (every hour)."""
        while self.running:
            try:
                self._adjust_strategy()
                await asyncio.sleep(3600)  # Every hour
            except Exception as e:
                log.tprint(f"❌ Learning error: {e}")
                await asyncio.sleep(3600)
    
    async def _report_loop(self):
        """Report progress (every 10 min)."""
        while self.running:
            try:
                uptime = time.time() - self.system_started
                summary = self.executor.execution_summary()
                health = self.monitor.metrics_summary()
                
                log.tprint(f"\n📊 Status (uptime: {uptime/3600:.1f}h)")
                log.tprint(f"   Total improvements: {self.total_improvements}")
                log.tprint(f"   Success rate: {summary['success_rate']:.0f}%")
                log.tprint(f"   Queue size: {len(self.improvement_queue)}")
                log.tprint(f"   Build: {health['build_time_s']} | Tests: {health['test_pass_rate']}")
                
                await asyncio.sleep(600)  # Every 10 minutes
            except Exception as e:
                log.tprint(f"❌ Reporting error: {e}")
                await asyncio.sleep(600)
    
    def _analyze_codebase(self) -> list[Improvement]:
        """Analyze codebase and return improvement opportunities."""
        improvements = []
        
        health = self.monitor.check_health()
        
        # Test coverage
        if health.test_coverage < 80:
            improvements.append(Improvement(
                type="test_coverage",
                title=f"Increase test coverage ({health.test_coverage:.0f}% → 80%)",
                description="Add missing tests to reach 80% coverage threshold",
                severity="high",
                task_spec="Identify untested code paths and add unit tests to reach 80% coverage. Focus on critical paths first.",
                success_rate=self.executor.success_rate("test_coverage")
            ))
        
        # Performance
        if health.build_time_s > 12:
            improvements.append(Improvement(
                type="performance",
                title=f"Optimize build time ({health.build_time_s:.1f}s → <10s)",
                description="Reduce build time by optimizing code or configuration",
                severity="medium",
                task_spec="Profile the build and identify slow steps. Optimize without breaking functionality.",
                success_rate=self.executor.success_rate("performance")
            ))
        
        # Code quality
        if health.linting_errors > 10:
            improvements.append(Improvement(
                type="code_quality",
                title=f"Fix linting errors ({health.linting_errors} errors)",
                description="Fix ESLint and style issues",
                severity="medium",
                task_spec="Run ESLint and fix all fixable errors. Review and fix manually-requirable ones.",
                success_rate=self.executor.success_rate("code_quality")
            ))
        
        # Type safety
        if health.type_errors > 5:
            improvements.append(Improvement(
                type="type_safety",
                title=f"Fix TypeScript errors ({health.type_errors} errors)",
                description="Improve type safety",
                severity="high",
                task_spec="Run TypeScript compiler and fix all type errors. Improve type annotations.",
                success_rate=self.executor.success_rate("type_safety")
            ))
        
        # Agent improvements
        improvements.append(Improvement(
            type="agent_prompt",
            title="Improve designer_browser agent reliability",
            description="Enhance DESIGNER_BROWSER_PROMPT based on past failures",
            severity="medium",
            task_spec="Review designer_browser agent failure cases and improve the system prompt with better guidelines and examples.",
            success_rate=self.executor.success_rate("agent_prompt")
        ))
        
        # Documentation
        improvements.append(Improvement(
            type="documentation",
            title="Update API documentation",
            description="Ensure docs match current implementation",
            severity="low",
            task_spec="Review documentation and update any inconsistencies with implementation.",
            success_rate=self.executor.success_rate("documentation")
        ))
        
        return improvements
    
    def _is_safe_to_implement(self, improvement: Improvement) -> bool:
        """Check if improvement is safe to implement."""
        # High confidence if success rate > 70%
        if improvement.success_rate > 70:
            return True
        
        # Urgent issues: implement even with lower confidence
        if improvement.severity in ["urgent", "high"]:
            return improvement.success_rate >= 50
        
        # Medium: need >60% confidence
        if improvement.severity == "medium":
            return improvement.success_rate > 60
        
        # Low priority: high confidence needed
        return improvement.success_rate > 80
    
    def _queue_urgent_fix(self, issue: str) -> None:
        """Queue an urgent fix for critical issue."""
        urgent = Improvement(
            type="urgent_fix",
            title=f"Fix: {issue}",
            description=f"Critical issue detected: {issue}",
            severity="urgent",
            task_spec=f"Critical issue: {issue}. Fix immediately without breaking anything.",
            success_rate=0.0
        )
        
        # Add to front of queue
        self.improvement_queue.insert(0, urgent)
    
    def _adjust_strategy(self) -> None:
        """Adjust improvement strategy based on success rates."""
        summary = self.executor.execution_summary()
        
        # Log what works best
        best_types = sorted(
            summary['by_type'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        log.tprint("\n🎯 Success by type (last hour):")
        for type_, rate in best_types[:3]:
            log.tprint(f"   {type_}: {rate:.0f}%")
        
        # Could dynamically adjust queue based on this
        # For now, just log it
    
    def status(self) -> dict:
        """Return current system status."""
        uptime = time.time() - self.system_started
        summary = self.executor.execution_summary()
        
        return {
            'uptime_hours': uptime / 3600,
            'total_improvements': self.total_improvements,
            'queue_size': len(self.improvement_queue),
            'success_rate': summary['success_rate'],
            'metrics': self.monitor.metrics_summary(),
            'timestamp': datetime.now().isoformat()
        }


def create_system(project_dir: Path, worker_agent=None) -> ContinuousImprovementSystem:
    """Factory to create the continuous improvement system."""
    return ContinuousImprovementSystem(project_dir, worker_agent)
