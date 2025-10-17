"""
Test script for blob path parser
Validates that both path patterns are correctly parsed
"""

def parse_blob_path(blob_path):
    """
    Parse blob path and detect pattern type (standalone version for testing)

    Handles two patterns:
    - Pattern A (PartnerLed): partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz
    - Pattern B (Pay-as-you-go): {subscription}/{export_name}/{date_range}/{guid}/part_*.csv.gz

    Args:
        blob_path: Full blob path string

    Returns:
        dict or None: Parsed metadata or None if invalid
    """
    path_parts = blob_path.split('/')

    # Pattern A: partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz (5 parts)
    if len(path_parts) == 5 and path_parts[0].lower() == 'partnerled':
        return {
            'subscription_name': path_parts[1],
            'export_name': 'partnerled',
            'date_range': path_parts[2],
            'guid': path_parts[3],
            'path_pattern': 'partnerled',
        }

    # Pattern B: {subscription}/{export_name}/{date_range}/{guid}/part_*.csv.gz (5 parts)
    elif len(path_parts) == 5 and path_parts[0].lower() != 'partnerled':
        return {
            'subscription_name': path_parts[0],
            'export_name': path_parts[1],
            'date_range': path_parts[2],
            'guid': path_parts[3],
            'path_pattern': 'pay-as-you-go',
        }

    # Unknown pattern
    return None


def test_blob_path_parser():
    """Test the parse_blob_path function with both patterns"""

    # Test Pattern A (PartnerLed)
    path_a = "partnerled/PartnerLed-power_up/20251001-20251031/1f8d218f-f78f-4563-9c36-fdf38032206d/part_0_0001.csv.gz"
    result_a = parse_blob_path(path_a)

    print("=" * 80)
    print("Testing Pattern A (PartnerLed)")
    print("=" * 80)
    print(f"Input: {path_a}")
    print(f"\nParsed Result:")
    if result_a:
        for key, value in result_a.items():
            print(f"  {key:20s}: {value}")
    else:
        print("  [FAIL] FAILED TO PARSE")

    # Validate Pattern A
    assert result_a is not None, "Pattern A should be parsed"
    assert result_a['subscription_name'] == 'PartnerLed-power_up', "Wrong subscription name"
    assert result_a['export_name'] == 'partnerled', "Wrong export name"
    assert result_a['date_range'] == '20251001-20251031', "Wrong date range"
    assert result_a['guid'] == '1f8d218f-f78f-4563-9c36-fdf38032206d', "Wrong GUID"
    assert result_a['path_pattern'] == 'partnerled', "Wrong pattern type"
    print("\n[OK] Pattern A validation PASSED\n")

    # Test Pattern B (Pay-as-you-go)
    path_b = "Pay as you go - Tom Privat/CostFocus-PayasyouGo/20251001-20251031/621cbd02-17ab-4f6a-9d5b-e676b4c0dcb6/part_1_0001.csv.gz"
    result_b = parse_blob_path(path_b)

    print("=" * 80)
    print("Testing Pattern B (Pay-as-you-go)")
    print("=" * 80)
    print(f"Input: {path_b}")
    print(f"\nParsed Result:")
    if result_b:
        for key, value in result_b.items():
            print(f"  {key:20s}: {value}")
    else:
        print("  [FAIL] FAILED TO PARSE")

    # Validate Pattern B
    assert result_b is not None, "Pattern B should be parsed"
    assert result_b['subscription_name'] == 'Pay as you go - Tom Privat', "Wrong subscription name"
    assert result_b['export_name'] == 'CostFocus-PayasyouGo', "Wrong export name"
    assert result_b['date_range'] == '20251001-20251031', "Wrong date range"
    assert result_b['guid'] == '621cbd02-17ab-4f6a-9d5b-e676b4c0dcb6', "Wrong GUID"
    assert result_b['path_pattern'] == 'pay-as-you-go', "Wrong pattern type"
    print("\n[OK] Pattern B validation PASSED\n")

    # Test invalid path (3 parts)
    invalid_path_short = "invalid/path/structure.csv.gz"
    result_invalid_short = parse_blob_path(invalid_path_short)

    print("=" * 80)
    print("Testing Invalid Path (too short)")
    print("=" * 80)
    print(f"Input: {invalid_path_short}")
    print(f"\nParsed Result: {result_invalid_short}")
    assert result_invalid_short is None, "Invalid path should return None"
    print("\n[OK] Invalid path handling PASSED\n")

    # Test invalid path (6 parts)
    invalid_path_long = "too/many/parts/in/this/path.csv.gz"
    result_invalid_long = parse_blob_path(invalid_path_long)

    print("=" * 80)
    print("Testing Invalid Path (too long)")
    print("=" * 80)
    print(f"Input: {invalid_path_long}")
    print(f"\nParsed Result: {result_invalid_long}")
    assert result_invalid_long is None, "Invalid path should return None"
    print("\n[OK] Invalid path handling PASSED\n")

    print("=" * 80)
    print("SUCCESS: ALL TESTS PASSED!")
    print("=" * 80)
    print("\nSummary:")
    print("  [OK] Pattern A (partnerled) - correctly parsed")
    print("  [OK] Pattern B (pay-as-you-go) - correctly parsed")
    print("  [OK] Invalid paths - correctly rejected")
    print("\nBoth subscription export patterns are now supported!")

if __name__ == '__main__':
    test_blob_path_parser()
