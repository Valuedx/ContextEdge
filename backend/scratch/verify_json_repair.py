import json
import re

def repair_truncated_json(s: str) -> str:
    """Attempt to repair truncated JSON by closing open braces/brackets/quotes."""
    s = s.strip()
    if not s:
        return s
    
    # Remove trailing commas which frequently appear in truncated JSON
    s = re.sub(r',\s*$', '', s)
    
    stack = []
    is_in_string = False
    escaped = False
    
    for char in s:
        if is_in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                is_in_string = False
        else:
            if char == '"':
                is_in_string = True
            elif char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char == "}":
                if stack and stack[-1] == "}":
                    stack.pop()
            elif char == "]":
                if stack and stack[-1] == "]":
                    stack.pop()
    
    # Close unclosed string
    if is_in_string:
        s += '"'
    
    # Close unclosed objects/arrays in reverse order
    while stack:
        s += stack.pop()
        
    return s

# Test cases
test_cases = [
    ('{"name": "John", "age": 30', '{"name": "John", "age": 30}'),
    ('{"items": [{"id": 1}, {"id": 2', '{"items": [{"id": 1}, {"id": 2}]}'),
    ('{"message": "Hello worl', '{"message": "Hello worl"}'),
    ('{"data": {"nested": [1, 2,', '{"data": {"nested": [1, 2]}}'),
]

for truncated, expected in test_cases:
    repaired = repair_truncated_json(truncated)
    print(f"Truncated: {truncated}")
    print(f"Repaired:  {repaired}")
    try:
        json.loads(repaired)
        print("Status: [OK] Valid JSON")
    except Exception as e:
        print(f"Status: [ERROR] Invalid JSON - {e}")
    print("-" * 30)

# The specific user log case
user_log_fragment = '{\n  "episodes": [\n    {\n      "title": "VPN Certificate Expiry Incident",\n      "root_cause_summary": "VPN certificate expiration.",\n      "final_outcome": "Implied resolution of the certificate issue.",\n      "overall_confidence": 0.8,\n      "steps": [\n        {\n          "step_order": 1,\n          "step_type": "complaint",\n          "text": "Datadog alert \'VPN Certificate Expiry\' triggered.",\n          "observation": "VPN access failing due to expired certificate.",\n          "result_state": "'
print("User log case:")
repaired_user = repair_truncated_json(user_log_fragment)
print(f"Repaired: {repaired_user}")
try:
    data = json.loads(repaired_user)
    print("Status: [OK] Valid JSON")
    print(f"Episodes found: {len(data['episodes'])}")
except Exception as e:
    print(f"Status: [ERROR] Invalid JSON - {e}")
