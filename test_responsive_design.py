#!/usr/bin/env python3
"""
VinsDelux Responsive Design Testing

This script tests the responsive design implementation for the VinsDelux platform
across different screen sizes and devices.
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime


class ResponsiveDesignTester:
    """Test responsive design implementation for VinsDelux."""
    
    def __init__(self):
        self.css_path = Path("static/css/custom.css")
        self.results = []
        
    def log_result(self, test_name, status, message, details=None):
        """Log test result."""
        result = {
            "test": test_name,
            "status": status,
            "message": message,
            "details": details or {},
            "timestamp": datetime.now().isoformat()
        }
        self.results.append(result)
        
        status_icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⏭️"
        print(f"{status_icon} {test_name}: {message}")
    
    def test_breakpoint_coverage(self):
        """Test that all major breakpoints are covered."""
        if not self.css_path.exists():
            self.log_result("Breakpoint Coverage", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        # Define standard breakpoints
        breakpoints = {
            "Mobile": r"@media.*\(max-width:\s*480px\)",
            "Mobile Large": r"@media.*\(max-width:\s*576px\)",
            "Tablet": r"@media.*\(min-width:\s*768px\)",
            "Desktop": r"@media.*\(min-width:\s*1024px\)",
            "Large Desktop": r"@media.*\(min-width:\s*1200px\)"
        }
        
        found_breakpoints = {}
        for name, pattern in breakpoints.items():
            matches = re.findall(pattern, css_content, re.IGNORECASE)
            found_breakpoints[name] = len(matches) > 0
        
        coverage = sum(found_breakpoints.values())
        total = len(breakpoints)
        
        if coverage >= 3:
            self.log_result("Breakpoint Coverage", "PASS", 
                          f"Found {coverage}/{total} breakpoints", found_breakpoints)
        else:
            self.log_result("Breakpoint Coverage", "FAIL", 
                          f"Only {coverage}/{total} breakpoints found", found_breakpoints)
    
    def test_fluid_typography(self):
        """Test fluid typography implementation."""
        if not self.css_path.exists():
            self.log_result("Fluid Typography", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        typography_features = {
            "clamp_usage": len(re.findall(r'clamp\(', css_content)),
            "rem_units": len(re.findall(r'\d+(?:\.\d+)?rem', css_content)),
            "em_units": len(re.findall(r'\d+(?:\.\d+)?em', css_content)),
            "vw_units": len(re.findall(r'\d+(?:\.\d+)?vw', css_content)),
            "responsive_font_sizes": len(re.findall(r'@media.*font-size', css_content))
        }
        
        # Check if fluid typography is implemented
        has_fluid = (
            typography_features["clamp_usage"] > 0 or
            typography_features["vw_units"] > 0 or
            typography_features["responsive_font_sizes"] >= 2
        )
        
        if has_fluid:
            self.log_result("Fluid Typography", "PASS", 
                          "Fluid typography is implemented", typography_features)
        else:
            self.log_result("Fluid Typography", "FAIL", 
                          "No fluid typography found", typography_features)
    
    def test_flexible_layouts(self):
        """Test flexible layout implementation."""
        if not self.css_path.exists():
            self.log_result("Flexible Layouts", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        layout_features = {
            "flexbox": css_content.count("display: flex") + css_content.count("display:flex"),
            "grid": css_content.count("display: grid") + css_content.count("display:grid"),
            "justify_content": css_content.count("justify-content"),
            "align_items": css_content.count("align-items"),
            "flex_wrap": css_content.count("flex-wrap"),
            "gap": css_content.count("gap:"),
            "percentage_widths": len(re.findall(r'width:\s*\d+%', css_content))
        }
        
        flexibility_score = (
            (layout_features["flexbox"] > 0) +
            (layout_features["grid"] > 0) +
            (layout_features["justify_content"] > 2) +
            (layout_features["align_items"] > 2) +
            (layout_features["percentage_widths"] > 3)
        )
        
        if flexibility_score >= 3:
            self.log_result("Flexible Layouts", "PASS", 
                          f"Flexible layouts implemented (score: {flexibility_score}/5)", 
                          layout_features)
        else:
            self.log_result("Flexible Layouts", "FAIL", 
                          f"Limited flexible layouts (score: {flexibility_score}/5)", 
                          layout_features)
    
    def test_responsive_images(self):
        """Test responsive image implementation."""
        # Check CSS for responsive image styles
        if not self.css_path.exists():
            self.log_result("Responsive Images", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        image_features = {
            "max_width_100": "max-width: 100%" in css_content or "max-width:100%" in css_content,
            "height_auto": "height: auto" in css_content or "height:auto" in css_content,
            "object_fit": "object-fit" in css_content,
            "responsive_images": len(re.findall(r'@media.*img|@media.*image', css_content)),
            "picture_element": "<picture>" in css_content,
            "srcset_support": "srcset" in css_content
        }
        
        responsive_score = sum([
            image_features["max_width_100"],
            image_features["height_auto"],
            image_features["object_fit"],
            image_features["responsive_images"] > 0
        ])
        
        if responsive_score >= 2:
            self.log_result("Responsive Images", "PASS", 
                          f"Responsive images implemented (score: {responsive_score}/4)", 
                          image_features)
        else:
            self.log_result("Responsive Images", "FAIL", 
                          f"Limited responsive images (score: {responsive_score}/4)", 
                          image_features)
    
    def test_mobile_first_approach(self):
        """Test if mobile-first approach is used."""
        if not self.css_path.exists():
            self.log_result("Mobile First", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        # Count min-width vs max-width media queries
        min_width_queries = len(re.findall(r'@media.*min-width', css_content))
        max_width_queries = len(re.findall(r'@media.*max-width', css_content))
        
        # Mobile-first typically uses more min-width queries
        mobile_first_ratio = min_width_queries / (max_width_queries + 1)  # +1 to avoid division by zero
        
        analysis = {
            "min_width_queries": min_width_queries,
            "max_width_queries": max_width_queries,
            "ratio": round(mobile_first_ratio, 2),
            "mobile_first": mobile_first_ratio >= 1.0
        }
        
        if analysis["mobile_first"]:
            self.log_result("Mobile First", "PASS", 
                          f"Mobile-first approach detected (ratio: {analysis['ratio']})", 
                          analysis)
        else:
            self.log_result("Mobile First", "FAIL", 
                          f"Desktop-first approach detected (ratio: {analysis['ratio']})", 
                          analysis)
    
    def test_touch_friendly_design(self):
        """Test touch-friendly design elements."""
        if not self.css_path.exists():
            self.log_result("Touch Friendly", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        # Look for touch-friendly sizing
        touch_features = {
            "large_buttons": len(re.findall(r'(?:width|height|min-width|min-height):\s*(?:[4-9]\d|[1-9]\d{2,})px', css_content)),
            "padding_spacing": len(re.findall(r'padding:\s*(?:[1-9]\d|[2-9])px', css_content)),
            "hover_states": css_content.count(":hover"),
            "focus_states": css_content.count(":focus"),
            "active_states": css_content.count(":active"),
            "touch_action": css_content.count("touch-action")
        }
        
        touch_score = (
            (touch_features["large_buttons"] > 5) +
            (touch_features["padding_spacing"] > 5) +
            (touch_features["hover_states"] > 3) +
            (touch_features["focus_states"] > 0) +
            (touch_features["active_states"] > 0)
        )
        
        if touch_score >= 3:
            self.log_result("Touch Friendly", "PASS", 
                          f"Touch-friendly design implemented (score: {touch_score}/5)", 
                          touch_features)
        else:
            self.log_result("Touch Friendly", "FAIL", 
                          f"Limited touch-friendly features (score: {touch_score}/5)", 
                          touch_features)
    
    def test_performance_optimization(self):
        """Test performance optimizations for responsive design."""
        if not self.css_path.exists():
            self.log_result("Performance Optimization", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        # File size check
        file_size = len(css_content.encode('utf-8'))
        file_size_kb = file_size / 1024
        
        performance_features = {
            "file_size_kb": round(file_size_kb, 2),
            "efficient_selectors": len(re.findall(r'\.[a-zA-Z-]+\s*{', css_content)),  # Class selectors
            "avoid_deep_nesting": len(re.findall(r'[^{]*\s+[^{]*\s+[^{]*\s+[^{]*{', css_content)) < 10,
            "use_transforms": css_content.count("transform:") > 0,
            "minimize_reflows": css_content.count("width:") + css_content.count("height:") < css_content.count("transform:") + css_content.count("opacity:"),
            "compressed": file_size_kb < 50  # Under 50KB
        }
        
        performance_score = sum([
            performance_features["file_size_kb"] < 100,
            performance_features["avoid_deep_nesting"],
            performance_features["use_transforms"],
            performance_features["compressed"]
        ])
        
        if performance_score >= 3:
            self.log_result("Performance Optimization", "PASS", 
                          f"Good performance optimization (score: {performance_score}/4)", 
                          performance_features)
        else:
            self.log_result("Performance Optimization", "FAIL", 
                          f"Performance could be improved (score: {performance_score}/4)", 
                          performance_features)
    
    def test_wine_specific_responsive(self):
        """Test wine-specific responsive features."""
        if not self.css_path.exists():
            self.log_result("Wine Responsive Features", "SKIP", "CSS file not found")
            return
        
        with open(self.css_path, 'r', encoding='utf-8') as f:
            css_content = f.read()
        
        wine_responsive_features = {
            "wine_plot_cards": "wine-plot-card" in css_content,
            "wine_cards_responsive": len(re.findall(r'@media.*wine-plot|wine-plot.*@media', css_content, re.DOTALL)),
            "wine_hero_responsive": len(re.findall(r'@media.*hero-section|hero-section.*@media', css_content, re.DOTALL)),
            "wine_timeline_responsive": len(re.findall(r'@media.*timeline|client-journey.*@media', css_content, re.DOTALL)),
            "wine_nav_responsive": len(re.findall(r'@media.*navbar|nav.*@media', css_content, re.DOTALL))
        }
        
        # Check for wine-specific responsive implementations
        wine_responsive = sum([
            wine_responsive_features["wine_plot_cards"],
            wine_responsive_features["wine_cards_responsive"] > 0,
            wine_responsive_features["wine_hero_responsive"] > 0,
            wine_responsive_features["wine_timeline_responsive"] > 0
        ])
        
        if wine_responsive >= 2:
            self.log_result("Wine Responsive Features", "PASS", 
                          f"Wine-specific responsive design implemented (score: {wine_responsive}/4)", 
                          wine_responsive_features)
        else:
            self.log_result("Wine Responsive Features", "FAIL", 
                          f"Limited wine-specific responsive features (score: {wine_responsive}/4)", 
                          wine_responsive_features)
    
    def run_all_tests(self):
        """Run all responsive design tests."""
        print("📱 Starting VinsDelux Responsive Design Tests")
        print("=" * 50)
        
        # Run all tests
        self.test_breakpoint_coverage()
        self.test_fluid_typography()
        self.test_flexible_layouts()
        self.test_responsive_images()
        self.test_mobile_first_approach()
        self.test_touch_friendly_design()
        self.test_performance_optimization()
        self.test_wine_specific_responsive()
        
        # Generate summary
        self.generate_summary()
    
    def generate_summary(self):
        """Generate test summary."""
        print("\n" + "=" * 50)
        print("📊 Responsive Design Test Summary")
        print("=" * 50)
        
        total_tests = len(self.results)
        passed_tests = len([r for r in self.results if r["status"] == "PASS"])
        failed_tests = len([r for r in self.results if r["status"] == "FAIL"])
        skipped_tests = len([r for r in self.results if r["status"] == "SKIP"])
        
        print(f"Total Tests: {total_tests}")
        print(f"✅ Passed: {passed_tests}")
        print(f"❌ Failed: {failed_tests}")
        print(f"⏭️  Skipped: {skipped_tests}")
        
        if total_tests > 0:
            success_rate = (passed_tests / (total_tests - skipped_tests)) * 100 if (total_tests - skipped_tests) > 0 else 0
            print(f"📈 Success Rate: {success_rate:.1f}%")
            
            if success_rate >= 80:
                print("🎉 Excellent responsive design implementation!")
            elif success_rate >= 60:
                print("👍 Good responsive design with room for improvement")
            else:
                print("⚠️  Responsive design needs significant improvements")
        
        # Save detailed report
        report_file = f"responsive_design_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w') as f:
            json.dump({
                "summary": {
                    "total": total_tests,
                    "passed": passed_tests,
                    "failed": failed_tests,
                    "skipped": skipped_tests,
                    "success_rate": success_rate if total_tests > 0 else 0
                },
                "tests": self.results
            }, f, indent=2)
        
        print(f"\n📄 Detailed report saved to: {report_file}")
        return success_rate if total_tests > 0 else 0


def main():
    """Main function to run responsive design tests."""
    tester = ResponsiveDesignTester()
    tester.run_all_tests()


if __name__ == "__main__":
    main()