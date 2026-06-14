"""
Module for schema-aware validation of worker outputs against provided specifications.
"""

from typing import Any, Dict, List, Tuple, Union

def validate_schema(spec_json_schema: Dict[str, Any], worker_output_json: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validates that the output fields are non-empty if declared in the schema.
    
    If a field is present in the spec but empty (e.g., an empty list or string) 
    in the worker output, the validation status is demoted from OK to PART.

    Args:
        spec_json_schema: The JSON schema defining expected fields.
        worker_output_json: The actual output produced by the worker.

    Returns:
        A tuple (ok: bool, errors: list) where ok is True if all declared 
        fields are non-empty, False otherwise.
    """
    errors = []
    is_ok = True

    # Extract fields from schema - assuming a structure where keys represent the output fields
    # and values describe the expected type or constraints.
    # For this implementation, we check if any field in 'spec_json_schema' 
    # has an empty value in 'worker_output_json'.

    for field_name, schema_def in spec_json_schema.items():
        # Check if the output actually contains this key
        if field_name in worker_output_json:
            val = worker_output_json[field_name]
            
            # Logic: Assert non-empty for list/dict/str types declared in schema
            # If it's empty, we demote to PART (represented here by is_ok = False)
            if isinstance(val, (list, dict, str)):
                if len(val) == 0:
                    is_ok = False
                    errors.append(f"Field '{field_name}' is non-empty in schema but empty in output.")
        else:
            # Field missing entirely from output?
            # Depending on strictness, this could also be an error or a PART status.
            # The requirement specifically mentions "Empty declared field -> demote OK to PART".
            pass

    return is_ok, errors

if __name__ == "__main__":
    # Test cases
    spec = {
        "security_bugs": [], # Declared as list
        "summary": "text"
    }
    
    output_ok = {
        "security_bugs": ["SQL Injection", "XSS"],
        "summary": "Found two bugs."
    }
    
    output_part = {
        "security_bugs": [], # Empty list - should demote
        "summary": "No bugs found."
    }

    ok1, errs1 = validate_schema(spec, output_ok)
    print(f"Test 1 (OK): ok={ok1}, errors={errs1}")
    assert ok1 is True

    ok2, errs2 = validate_schema(spec, output_part)
    print(f"Test 2 (PART): ok={ok2}, errors={errs2}")
    assert ok2 is False
    print("All tests passed!")
