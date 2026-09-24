"""Task 3.7 -- minimal protected API prototype (gate F10).

API Gateway HTTP API JWT authorizer validates the Cognito token BEFORE this
code ever runs (signature, issuer, audience, expiry). This handler only makes
the role decision -- proving design section 6.2's rule that hiding a control
client-side is never authorization; the server checks the role on every call.
"""
import json


def _claims(event):
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def _groups(claims):
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return raw
    # HTTP API JWT authorizer flattens list claims to a string like "[admin]".
    return raw.strip("[]").split() if raw else []


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    route = event.get("routeKey", "")
    claims = _claims(event)
    groups = _groups(claims)
    subject = claims.get("sub")

    if route == "GET /health":
        # Deliberately NOT behind the authorizer -- proves an anonymous,
        # unauthenticated route can coexist with protected ones without
        # leaking anything sensitive.
        return _response(200, {"status": "ok", "authenticated": False})

    if route == "GET /protected/data":
        # Authorizer already rejected this call if the token was missing,
        # expired, or had the wrong issuer/audience. Reaching here means a
        # verified Cognito identity exists -- any authenticated role may read.
        if not subject:
            return _response(401, {"error": "unauthenticated"})
        return _response(
            200,
            {
                "race_data": "placeholder result set",
                "requested_by_actor": subject[:8] + "...",
                "role": groups,
            },
        )

    if route == "POST /admin/action":
        if not subject:
            return _response(401, {"error": "unauthenticated"})
        if "admin" not in groups:
            # This is the server-side check design section 6.2 requires --
            # a valid, authenticated, non-admin token must still be refused.
            return _response(
                403, {"error": "forbidden", "reason": "admin role required"}
            )
        return _response(200, {"status": "action executed", "actor": subject[:8] + "..."})

    return _response(404, {"error": "not found"})
