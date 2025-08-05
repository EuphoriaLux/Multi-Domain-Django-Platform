#!/usr/bin/env python3
"""
VinsDelux UI Testing Suite

This script provides comprehensive testing for the VinsDelux platform's
UI improvements, styling enhancements, and user experience features.
"""

import os
import sys
import subprocess
import time
import requests
from pathlib import Path
import json
from datetime import datetime


class VinsDeluxUITester:
    """Main testing class for VinsDelux UI improvements."""
    
    def __init__(self):
        self.base_url = "http://127.0.0.1:8000"
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "tests": [],
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0
            }
        }
    
    def log_test(self, test_name, status, message="", details=None):
        """Log test results."""
        test_result = {
            "name": test_name,
            "status": status,  # "PASS", "FAIL", "SKIP"
            "message": message,
            "details": details or {}
        }
        self.results["tests"].append(test_result)
        self.results["summary"]["total"] += 1
        self.results["summary"][status.lower() + ("ed" if status != "SKIP" else "ped")] += 1
        
        # Print result
        status_symbol = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏭️"
        print(f"{status_symbol} {test_name}: {message}")
    
    def test_server_running(self):
        """Test if Django server is running."""
        try:
            response = requests.get(self.base_url, timeout=5)
            if response.status_code == 200:
                self.log_test("Server Status", "PASS", "Django server is running")
                return True
            else:
                self.log_test("Server Status", "FAIL", f"Server returned status {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            self.log_test("Server Status", "FAIL", f"Cannot connect to server: {e}")
            return False
    
    def test_css_file_exists(self):
        """Test if custom CSS file exists and contains VinsDelux styles."""
        css_path = Path("static/css/custom.css")
        
        if not css_path.exists():
            self.log_test("CSS File Exists", "FAIL", "custom.css file not found")
            return False
        
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            
            # Check for wine-related styles
            wine_indicators = [
                "--wine-burgundy",
                "--wine-gold",
                "wine-plot-card",
                "glass-card",
                "backdrop-filter"
            ]
            
            found_indicators = []
            for indicator in wine_indicators:
                if indicator in css_content:
                    found_indicators.append(indicator)
            
            if len(found_indicators) >= 3:
                self.log_test("CSS Wine Styles", "PASS", 
                            f"Found {len(found_indicators)}/5 wine style indicators",
                            {"found": found_indicators})
            else:
                self.log_test("CSS Wine Styles", "FAIL", 
                            f"Only found {len(found_indicators)}/5 wine style indicators",
                            {"found": found_indicators})
            
            return True
            
        except Exception as e:
            self.log_test("CSS File Read", "FAIL", f"Error reading CSS file: {e}")
            return False
    
    def test_responsive_design(self):
        """Test responsive design media queries."""
        css_path = Path("static/css/custom.css")
        
        if not css_path.exists():
            self.log_test("Responsive Design", "SKIP", "CSS file not found")
            return False
        
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            
            media_queries = {
                "mobile": "@media (max-width: 480px)",
                "tablet": "@media (min-width: 768px)",
                "desktop": "@media (min-width: 1024px)"
            }
            
            found_queries = {}
            for device, query in media_queries.items():
                if query in css_content:
                    found_queries[device] = True
                else:
                    found_queries[device] = False
            
            responsive_score = sum(found_queries.values())
            
            if responsive_score >= 2:
                self.log_test("Responsive Media Queries", "PASS", 
                            f"Found {responsive_score}/3 responsive breakpoints",
                            found_queries)
            else:
                self.log_test("Responsive Media Queries", "FAIL", 
                            f"Only found {responsive_score}/3 responsive breakpoints",
                            found_queries)
            
        except Exception as e:
            self.log_test("Responsive Design", "FAIL", f"Error testing responsive design: {e}")
    
    def test_premium_typography(self):
        """Test premium typography implementation."""
        css_path = Path("static/css/custom.css")
        
        if not css_path.exists():
            self.log_test("Premium Typography", "SKIP", "CSS file not found")
            return False
        
        try:
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            
            typography_features = {
                "Playfair Display": "Playfair Display" in css_content,
                "Lato": "Lato" in css_content,
                "Font weights": "font-weight: 700" in css_content and "font-weight: 300" in css_content,
                "Custom properties": ":root" in css_content,
                "Fluid typography": "clamp(" in css_content
            }
            
            typography_score = sum(typography_features.values())
            
            if typography_score >= 3:
                self.log_test("Premium Typography", "PASS", 
                            f"Found {typography_score}/5 typography features",
                            typography_features)
            else:
                self.log_test("Premium Typography", "FAIL", 
                            f"Only found {typography_score}/5 typography features",
                            typography_features)
            
        except Exception as e:
            self.log_test("Premium Typography", "FAIL", f"Error testing typography: {e}")
    
    def test_vinsdelux_page_accessibility(self):
        """Test VinsDelux page accessibility."""
        try:
            vinsdelux_url = f"{self.base_url}/vinsdelux/"
            response = requests.get(vinsdelux_url, timeout=10)
            
            if response.status_code == 200:
                html_content = response.text
                
                # Basic accessibility checks
                accessibility_features = {
                    "Has title": "<title>" in html_content,
                    "Has meta viewport": "viewport" in html_content,
                    "Has alt attributes": 'alt=' in html_content,
                    "Has semantic HTML": any(tag in html_content for tag in ["<header>", "<main>", "<nav>", "<section>"]),
                    "Has ARIA labels": "aria-" in html_content
                }
                
                accessibility_score = sum(accessibility_features.values())
                
                if accessibility_score >= 4:
                    self.log_test("VinsDelux Accessibility", "PASS", 
                                f"Found {accessibility_score}/5 accessibility features",
                                accessibility_features)
                else:
                    self.log_test("VinsDelux Accessibility", "FAIL", 
                                f"Only found {accessibility_score}/5 accessibility features",
                                accessibility_features)
            else:
                self.log_test("VinsDelux Page Load", "FAIL", 
                            f"VinsDelux page returned status {response.status_code}")
                
        except requests.exceptions.RequestException:
            self.log_test("VinsDelux Page Access", "SKIP", 
                        "VinsDelux page not accessible (may not be configured)")
    
    def test_static_files_loading(self):
        """Test that static files load correctly."""
        static_files = [
            "/static/css/custom.css",
            "/static/bootstrap/css/bootstrap.min.css",
            "/static/fontawesome/css/all.min.css"
        ]
        
        successful_loads = 0
        file_results = {}
        
        for static_file in static_files:
            try:
                url = f"{self.base_url}{static_file}"
                response = requests.get(url, timeout=5)
                
                if response.status_code == 200:
                    file_results[static_file] = "✅ Loaded"
                    successful_loads += 1
                else:
                    file_results[static_file] = f"❌ Status {response.status_code}"
                    
            except requests.exceptions.RequestException as e:
                file_results[static_file] = f"❌ Error: {str(e)[:50]}"
        
        if successful_loads >= 2:
            self.log_test("Static Files Loading", "PASS", 
                        f"{successful_loads}/{len(static_files)} static files loaded",
                        file_results)
        else:
            self.log_test("Static Files Loading", "FAIL", 
                        f"Only {successful_loads}/{len(static_files)} static files loaded",
                        file_results)
    
    def test_css_performance(self):
        """Test CSS file performance metrics."""
        css_path = Path("static/css/custom.css")
        
        if not css_path.exists():
            self.log_test("CSS Performance", "SKIP", "CSS file not found")
            return
        
        try:
            file_size = css_path.stat().st_size
            
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            
            # Performance metrics
            metrics = {
                "file_size_kb": round(file_size / 1024, 2),
                "lines_count": len(css_content.splitlines()),
                "selectors_count": css_content.count('{'),
                "media_queries": css_content.count('@media'),
                "animations": css_content.count('transition:') + css_content.count('animation:')
            }
            
            # Performance criteria
            performance_good = (
                metrics["file_size_kb"] < 100 and  # Under 100KB
                metrics["media_queries"] >= 2 and   # Has responsive design
                metrics["animations"] >= 5          # Has smooth interactions
            )
            
            if performance_good:
                self.log_test("CSS Performance", "PASS", 
                            "CSS meets performance criteria", metrics)
            else:
                self.log_test("CSS Performance", "FAIL", 
                            "CSS performance could be improved", metrics)
                
        except Exception as e:
            self.log_test("CSS Performance", "FAIL", f"Error analyzing CSS performance: {e}")
    
    def run_django_tests(self):
        """Run Django unit tests for VinsDelux."""
        try:
            # Run specific VinsDelux tests
            result = subprocess.run([
                sys.executable, "manage.py", "test", "vinsdelux.test_styling", "-v", "2"
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                test_output = result.stdout
                # Parse test results
                passed_tests = test_output.count('OK')
                failed_tests = test_output.count('FAIL') + test_output.count('ERROR')
                
                self.log_test("Django Unit Tests", "PASS", 
                            f"Django tests completed successfully",
                            {"stdout": test_output[:500] + "..." if len(test_output) > 500 else test_output})
            else:
                self.log_test("Django Unit Tests", "FAIL", 
                            f"Django tests failed with return code {result.returncode}",
                            {"stderr": result.stderr[:500] + "..." if len(result.stderr) > 500 else result.stderr})
                
        except subprocess.TimeoutExpired:
            self.log_test("Django Unit Tests", "FAIL", "Django tests timed out")
        except Exception as e:
            self.log_test("Django Unit Tests", "FAIL", f"Error running Django tests: {e}")
    
    def run_all_tests(self):
        """Run all VinsDelux UI tests."""
        print("🍷 Starting VinsDelux UI Testing Suite")
        print("=" * 50)
        
        # Core functionality tests
        server_running = self.test_server_running()
        
        if server_running:
            self.test_static_files_loading()
            self.test_vinsdelux_page_accessibility()
        
        # CSS and styling tests
        self.test_css_file_exists()
        self.test_responsive_design()
        self.test_premium_typography()
        self.test_css_performance()
        
        # Django unit tests
        self.test_django_tests()
        
        # Generate report
        self.generate_report()
    
    def test_django_tests(self):
        """Wrapper for Django tests to handle virtual environment."""
        if os.path.exists('.venv'):
            print("\n🧪 Running Django Unit Tests...")
            self.run_django_tests()
        else:
            self.log_test("Django Environment", "SKIP", "Virtual environment not detected")
    
    def generate_report(self):
        """Generate final test report."""
        print("\n" + "=" * 50)
        print("📊 VinsDelux UI Testing Report")
        print("=" * 50)
        
        summary = self.results["summary"]
        total = summary["total"]
        passed = summary["passed"]
        failed = summary["failed"]
        skipped = summary["skipped"]
        
        print(f"Total Tests: {total}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        print(f"⏭️  Skipped: {skipped}")
        
        if total > 0:
            success_rate = (passed / total) * 100
            print(f"📈 Success Rate: {success_rate:.1f}%")
            
            if success_rate >= 80:
                print("🎉 VinsDelux UI improvements are working well!")
            elif success_rate >= 60:
                print("⚠️  VinsDelux UI has some issues to address")
            else:
                print("🚨 VinsDelux UI needs significant improvements")
        
        # Save detailed report
        report_file = f"vinsdelux_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📄 Detailed report saved to: {report_file}")
        
        return success_rate if total > 0 else 0


def main():
    """Main function to run VinsDelux UI tests."""
    tester = VinsDeluxUITester()
    success_rate = tester.run_all_tests()
    
    # Exit with appropriate code
    if success_rate >= 80:
        sys.exit(0)  # Success
    else:
        sys.exit(1)  # Some issues found


if __name__ == "__main__":
    main()