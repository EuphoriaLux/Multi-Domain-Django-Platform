#!/usr/bin/env python3
"""
VinsDelux Complete Testing Suite

This master script runs all VinsDelux tests including styling, functionality,
responsive design, and generates a comprehensive report.
"""

import os
import sys
import subprocess
import json
import time
from datetime import datetime
from pathlib import Path


class VinsDeluxTestSuite:
    """Master test suite for all VinsDelux improvements."""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.results = {
            "test_suite": "VinsDelux Complete Testing",
            "start_time": self.start_time.isoformat(),
            "tests": {},
            "summary": {
                "total_test_categories": 0,
                "passed_categories": 0,
                "failed_categories": 0,
                "overall_success_rate": 0
            }
        }
    
    def print_header(self):
        """Print test suite header."""
        print("🍷" * 20)
        print("🍷 VinsDelux Complete Testing Suite 🍷")
        print("🍷" * 20)
        print(f"Started at: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
    
    def run_ui_tests(self):
        """Run UI and styling tests."""
        print("\n🎨 Running UI & Styling Tests...")
        print("-" * 40)
        
        try:
            # Import and run UI tests
            sys.path.append('.')
            from test_vinsdelux_ui import VinsDeluxUITester
            
            ui_tester = VinsDeluxUITester()
            success_rate = ui_tester.run_all_tests()
            
            self.results["tests"]["ui_styling"] = {
                "success_rate": success_rate,
                "status": "PASS" if success_rate >= 70 else "FAIL",
                "details": ui_tester.results
            }
            
            return success_rate >= 70
            
        except Exception as e:
            print(f"❌ UI Tests failed: {e}")
            self.results["tests"]["ui_styling"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": str(e)
            }
            return False
    
    def run_responsive_tests(self):
        """Run responsive design tests."""
        print("\n📱 Running Responsive Design Tests...")
        print("-" * 40)
        
        try:
            from test_responsive_design import ResponsiveDesignTester
            
            responsive_tester = ResponsiveDesignTester()
            success_rate = responsive_tester.run_all_tests()
            
            self.results["tests"]["responsive_design"] = {
                "success_rate": success_rate,
                "status": "PASS" if success_rate >= 70 else "FAIL",
                "details": responsive_tester.results
            }
            
            return success_rate >= 70
            
        except Exception as e:
            print(f"❌ Responsive Tests failed: {e}")
            self.results["tests"]["responsive_design"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": str(e)
            }
            return False
    
    def run_django_tests(self):
        """Run Django unit tests."""
        print("\n🧪 Running Django Unit Tests...")
        print("-" * 40)
        
        try:
            # Activate virtual environment and run Django tests
            if os.path.exists('.venv'):
                if os.name == 'nt':  # Windows
                    activate_cmd = '.venv\\Scripts\\activate.bat'
                else:  # Unix-like
                    activate_cmd = 'source .venv/bin/activate'
                
                # Run VinsDelux specific tests
                cmd = f"{activate_cmd} && python manage.py test vinsdelux.test_styling -v 2"
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
                
                if result.returncode == 0:
                    # Parse Django test output
                    test_output = result.stdout
                    failed_count = test_output.count('FAIL') + test_output.count('ERROR')
                    ok_count = test_output.count(' ... ok')
                    total_tests = ok_count + failed_count
                    
                    success_rate = (ok_count / total_tests * 100) if total_tests > 0 else 100
                    
                    self.results["tests"]["django_unit"] = {
                        "success_rate": success_rate,
                        "status": "PASS" if success_rate >= 80 else "FAIL",
                        "total_tests": total_tests,
                        "passed": ok_count,
                        "failed": failed_count,
                        "output": test_output[:1000]  # First 1000 chars
                    }
                    
                    print(f"✅ Django Tests: {ok_count}/{total_tests} passed ({success_rate:.1f}%)")
                    return success_rate >= 80
                else:
                    print(f"❌ Django Tests failed with return code {result.returncode}")
                    self.results["tests"]["django_unit"] = {
                        "success_rate": 0,
                        "status": "FAIL",
                        "error": result.stderr[:500]
                    }
                    return False
            else:
                print("⏭️  Django Tests skipped (no virtual environment)")
                self.results["tests"]["django_unit"] = {
                    "success_rate": 0,
                    "status": "SKIP",
                    "message": "No virtual environment found"
                }
                return True  # Don't count as failure
                
        except subprocess.TimeoutExpired:
            print("❌ Django Tests timed out")
            self.results["tests"]["django_unit"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": "Tests timed out"
            }
            return False
        except Exception as e:
            print(f"❌ Django Tests error: {e}")
            self.results["tests"]["django_unit"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": str(e)
            }
            return False
    
    def run_css_validation(self):
        """Run CSS validation tests."""
        print("\n🎨 Running CSS Validation...")
        print("-" * 40)
        
        try:
            css_path = Path("static/css/custom.css")
            if not css_path.exists():
                print("❌ CSS file not found")
                self.results["tests"]["css_validation"] = {
                    "success_rate": 0,
                    "status": "FAIL",
                    "error": "CSS file not found"
                }
                return False
            
            # Basic CSS validation
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            
            validation_checks = {
                "wine_colors": any(color in css_content for color in ['#722f37', '#d4af37', '#8b0000']),
                "premium_fonts": 'Playfair Display' in css_content or 'Lato' in css_content,
                "responsive_queries": '@media' in css_content,
                "modern_css": 'backdrop-filter' in css_content or 'clamp(' in css_content,
                "wine_classes": 'wine-plot' in css_content or 'glass-card' in css_content
            }
            
            passed_checks = sum(validation_checks.values())
            total_checks = len(validation_checks)
            success_rate = (passed_checks / total_checks) * 100
            
            self.results["tests"]["css_validation"] = {
                "success_rate": success_rate,
                "status": "PASS" if success_rate >= 80 else "FAIL",
                "checks": validation_checks,
                "passed": passed_checks,
                "total": total_checks
            }
            
            print(f"✅ CSS Validation: {passed_checks}/{total_checks} checks passed ({success_rate:.1f}%)")
            return success_rate >= 80
            
        except Exception as e:
            print(f"❌ CSS Validation error: {e}")
            self.results["tests"]["css_validation"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": str(e)
            }
            return False
    
    def run_server_health_check(self):
        """Check if Django server is running and responsive."""
        print("\n🔍 Running Server Health Check...")
        print("-" * 40)
        
        try:
            import requests
            
            # Test main server
            main_response = requests.get("http://127.0.0.1:8000", timeout=10)
            main_healthy = main_response.status_code == 200
            
            # Test VinsDelux endpoint if it exists
            vinsdelux_healthy = False
            try:
                vinsdelux_response = requests.get("http://127.0.0.1:8000/vinsdelux/", timeout=5)
                vinsdelux_healthy = vinsdelux_response.status_code == 200
            except:
                vinsdelux_healthy = False  # Endpoint might not exist yet
            
            # Test static files
            static_files = [
                "/static/css/custom.css",
                "/static/bootstrap/css/bootstrap.min.css"
            ]
            
            static_healthy = 0
            for static_file in static_files:
                try:
                    static_response = requests.get(f"http://127.0.0.1:8000{static_file}", timeout=5)
                    if static_response.status_code == 200:
                        static_healthy += 1
                except:
                    pass
            
            health_score = (
                (main_healthy * 40) +
                (vinsdelux_healthy * 30) +
                ((static_healthy / len(static_files)) * 30)
            )
            
            self.results["tests"]["server_health"] = {
                "success_rate": health_score,
                "status": "PASS" if health_score >= 70 else "FAIL",
                "main_server": main_healthy,
                "vinsdelux_endpoint": vinsdelux_healthy,
                "static_files": f"{static_healthy}/{len(static_files)}"
            }
            
            print(f"✅ Server Health: {health_score:.1f}% healthy")
            return health_score >= 70
            
        except Exception as e:
            print(f"❌ Server Health Check failed: {e}")
            self.results["tests"]["server_health"] = {
                "success_rate": 0,
                "status": "FAIL",
                "error": str(e)
            }
            return False
    
    def generate_final_report(self):
        """Generate comprehensive final report."""
        end_time = datetime.now()
        duration = end_time - self.start_time
        
        self.results["end_time"] = end_time.isoformat()
        self.results["duration_seconds"] = duration.total_seconds()
        
        # Calculate overall statistics
        test_categories = list(self.results["tests"].keys())
        passed_categories = [cat for cat, result in self.results["tests"].items() 
                           if result.get("status") == "PASS"]
        failed_categories = [cat for cat, result in self.results["tests"].items() 
                           if result.get("status") == "FAIL"]
        
        # Calculate weighted success rate
        success_rates = []
        weights = {"ui_styling": 0.3, "responsive_design": 0.25, "css_validation": 0.2, 
                  "django_unit": 0.15, "server_health": 0.1}
        
        for category, result in self.results["tests"].items():
            if result.get("status") != "SKIP":
                weight = weights.get(category, 0.1)
                success_rates.append(result.get("success_rate", 0) * weight)
        
        overall_success = sum(success_rates) if success_rates else 0
        
        self.results["summary"] = {
            "total_test_categories": len(test_categories),
            "passed_categories": len(passed_categories),
            "failed_categories": len(failed_categories),
            "overall_success_rate": round(overall_success, 2),
            "duration_minutes": round(duration.total_seconds() / 60, 2)
        }
        
        # Print final report
        print("\n" + "🍷" * 20)
        print("🍷 FINAL TEST REPORT 🍷")
        print("🍷" * 20)
        print(f"Test Duration: {self.results['summary']['duration_minutes']} minutes")
        print(f"Total Categories: {len(test_categories)}")
        print(f"✅ Passed: {len(passed_categories)}")
        print(f"❌ Failed: {len(failed_categories)}")
        print(f"📊 Overall Success Rate: {overall_success:.1f}%")
        
        # Print category details
        print("\n📋 Category Breakdown:")
        for category, result in self.results["tests"].items():
            status_icon = "✅" if result.get("status") == "PASS" else "❌" if result.get("status") == "FAIL" else "⏭️"
            success_rate = result.get("success_rate", 0)
            print(f"  {status_icon} {category.replace('_', ' ').title()}: {success_rate:.1f}%")
        
        # Overall assessment
        print("\n🎯 Overall Assessment:")
        if overall_success >= 85:
            print("🎉 EXCELLENT! VinsDelux improvements are working beautifully!")
            print("   The premium wine experience is fully implemented.")
        elif overall_success >= 70:
            print("👍 GOOD! VinsDelux improvements are mostly working well.")
            print("   Some minor issues to address for optimal experience.")
        elif overall_success >= 50:
            print("⚠️  NEEDS WORK! VinsDelux has significant issues to resolve.")
            print("   Consider reviewing failed tests and implementing fixes.")
        else:
            print("🚨 CRITICAL! VinsDelux improvements need major attention.")
            print("   Multiple systems are not working as expected.")
        
        # Save comprehensive report
        report_filename = f"vinsdelux_complete_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_filename, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📄 Complete report saved to: {report_filename}")
        
        return overall_success
    
    def run_complete_suite(self):
        """Run the complete test suite."""
        self.print_header()
        
        # Run all test categories
        test_results = []
        
        test_results.append(self.run_server_health_check())
        test_results.append(self.run_css_validation())
        test_results.append(self.run_ui_tests())
        test_results.append(self.run_responsive_tests())
        test_results.append(self.run_django_tests())
        
        # Generate final report
        overall_success = self.generate_final_report()
        
        # Return appropriate exit code
        return 0 if overall_success >= 70 else 1


def main():
    """Main function to run complete VinsDelux test suite."""
    test_suite = VinsDeluxTestSuite()
    exit_code = test_suite.run_complete_suite()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()