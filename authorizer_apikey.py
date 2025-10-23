import os

# Environment variable holding the valid key
VALID_API_KEY = os.environ.get("VALID_API_KEY", "")

def lambda_handler(event, context):
    """
    Lambda Authorizer for HTTP API (v2)
    Expects the header: x-api-key: <your-key>
    """

    # Debug logging (use carefully or remove in production)
    #print("Received event:", event)

    # Extract the API key from headers
    headers = event.get("headers", {}) or {}
    api_key = headers.get("x-api-key")

    # Basic validation
    if not api_key:
        return generate_auth_response("Unauthorized", allow=False)

    # Compare with stored key
    if api_key == VALID_API_KEY:
        return generate_auth_response("AuthorizedUser", allow=True)
    else:
        return generate_auth_response("Unauthorized", allow=False)


def generate_auth_response(principal_id, allow=True):
    """
    Build an IAM policy-style response.
    For HTTP APIs, this must include 'isAuthorized' field.
    """
    return {
        "isAuthorized": allow,
        "context": {
            "principalId": principal_id
        }
    }
