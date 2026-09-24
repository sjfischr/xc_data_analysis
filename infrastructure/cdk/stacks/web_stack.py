"""Web, API, authentication (Task 15.2/18.1): Cognito, ECR, App Runner for
the FastAPI container, and S3 + CloudFront for the static Next.js export.

Real CDK, deployed this session with explicit owner authorization (2026-09-
22, after Task 17.4 acceptance) -- unlike every other CDK stack this
project has written so far, this one is meant to actually be applied, not
just synthesized. See ``docs/operations-and-acceptance-readiness.md`` for
the full record of what was and wasn't deployed.

**Deliberate deviation from design.md's original "CloudFront + API
Gateway HTTP API + Lambda BFF" choice**: this stack uses App Runner
instead of API Gateway + Lambda for the API. Reason: Task 13.5's WebSocket
handler uses FastAPI/Starlette's native long-lived WebSocket support,
which has no clean mapping onto API Gateway WebSocket API's
per-message-Lambda-invocation model without a substantial rewrite; App
Runner runs the FastAPI app as an ordinary long-lived ASGI server (the
same `uvicorn` process every other environment in this repo uses),
supporting WebSockets with no adapter at all. App Runner is still
request-driven/autoscaling (Requirement 15.2's "favor... request-driven
services"), just not to zero -- a real, documented tradeoff, not an
oversight.

**No custom domain**: no domain name was provided for this deployment, so
CloudFront's default ``*.cloudfront.net`` domain (HTTPS out of the box, no
ACM certificate needed) and App Runner's default ``*.awsapprunner.com``
domain are the real, live URLs -- there is no DNS cutover in this
deployment. See stack outputs for the actual URLs after deploy.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apprunner as apprunner
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct


class LocalFrontendBuildError(ValueError):
    pass


def assert_production_frontend_build(web_source_dir: str) -> None:
    """Refuse to publish a frontend built for local development.

    Found 2026-09-24: `web/out` held a local test build (API base
    `http://localhost:8010`, dev sign-in) when the production deploy ran,
    so the live site called localhost and showed only "Redirecting to sign
    in". NEXT_PUBLIC_* values are baked into the JS at build time, so the
    built files themselves are the thing to check.
    """
    chunks = Path(web_source_dir) / "_next" / "static"
    offenders = sorted(
        str(path.relative_to(web_source_dir))
        for path in chunks.rglob("*.js")
        if "http://localhost" in path.read_text(encoding="utf-8", errors="ignore")
    )
    if offenders:
        raise LocalFrontendBuildError(
            f"{web_source_dir} is a local development build (it calls "
            f"http://localhost, e.g. in {offenders[0]}). Rebuild with "
            "NEXT_PUBLIC_AUTH_MODE=cognito and NEXT_PUBLIC_API_BASE_URL set to "
            "the production API before deploying."
        )


class WebStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket_name: str,
        web_source_dir: str | None = None,
        deploy_api_service: bool = True,
        agent_runtime_arn: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        # --- Cognito: invite-only, admin/viewer groups (Task 3.7's proven
        # pattern, promoted to production). AllowAdminCreateUserOnly means
        # self-registration is refused -- confirmed live in the Task 3.7
        # gate, replicated here as CDK rather than re-derived.
        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        cognito.CfnUserPoolGroup(
            self, "AdminGroup", user_pool_id=self.user_pool.user_pool_id, group_name="admin"
        )
        cognito.CfnUserPoolGroup(
            self, "ViewerGroup", user_pool_id=self.user_pool.user_pool_id, group_name="viewer"
        )
        # Task 19.4: per-user access to Ask the Data. Admins always have it;
        # anyone else needs this group (read at login, api/routers/auth.py).
        cognito.CfnUserPoolGroup(
            self,
            "AgentUsersGroup",
            user_pool_id=self.user_pool.user_pool_id,
            group_name="agent-users",
            description="May use Ask the Data (the analytics agent)",
        )

        # A domain prefix this stack picks itself (account-scoped, so it is
        # unique without needing to look anything up) rather than
        # `self.user_pool_domain.domain_name` -- since account/region are
        # concrete strings here (app.py always passes a real
        # cdk.Environment), this is a real Python string, not a deploy-time
        # token, which is what lets Task 18.2's OAuth login route below
        # compute it too without needing it threaded through as an env var.
        self.cognito_domain_prefix = f"xc-platform-{self.account}"
        self.cognito_domain_host = (
            f"{self.cognito_domain_prefix}.auth.{self.region}.amazoncognito.com"
        )
        cognito.UserPoolDomain(
            self,
            "UserPoolDomain",
            user_pool=self.user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=self.cognito_domain_prefix
            ),
        )

        # --- ECR: the API's container image.
        self.repository = ecr.Repository(
            self,
            "ApiRepository",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
        )

        # --- Frontend: private S3 bucket (Task 15.1's WebBucket pattern),
        # served only through CloudFront (an Origin Access Control keeps
        # the bucket itself non-public even though CloudFront can read
        # it) -- the Next.js static export (`web/out/`), deployed if a
        # source directory is given. Created here, before ApiService, so
        # its domain name can be passed to ApiService as XC_FRONTEND_ORIGIN
        # (auth.py's callback() redirect target) -- it has no dependency on
        # anything created after this point, so moving it earlier costs
        # nothing.
        self.web_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.web_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        function=cloudfront.Function(
                            self,
                            "RewriteCleanUrls",
                            code=cloudfront.FunctionCode.from_file(
                                file_path=str(
                                    Path(__file__).parent
                                    / "assets"
                                    / "rewrite_clean_urls.js"
                                )
                            ),
                            runtime=cloudfront.FunctionRuntime.JS_2_0,
                        ),
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            # A static-export SPA-ish app: an unknown path (client-side
            # route) still serves index.html rather than CloudFront's
            # default S3 403/404.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
        )
        if web_source_dir:
            assert_production_frontend_build(web_source_dir)
            s3_deployment.BucketDeployment(
                self,
                "FrontendDeployment",
                sources=[s3_deployment.Source.asset(web_source_dir)],
                destination_bucket=self.web_bucket,
                distribution=self.distribution,
                distribution_paths=["/*"],
            )

        # --- App Runner: the FastAPI container. Exactly one instance
        # (min=max=1) -- both the SQLite single-writer design (section 7)
        # and this deployment's accepted in-memory session/ticket-replay
        # store (run_production_api.py's documented limitation) require
        # it; auto-scaling to more than one instance would silently break
        # session consistency.
        self.instance_role = iam.Role(
            self,
            "ApiInstanceRole",
            assumed_by=iam.ServicePrincipal("tasks.apprunner.amazonaws.com"),
            description="Least-privilege runtime role for the deployed FastAPI container",
        )
        self.data_bucket = s3.Bucket.from_bucket_name(
            self, "ImportedDataBucket", data_bucket_name
        )
        self.data_bucket.grant_read_write(self.instance_role)
        self.instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/xc-platform/*"
                ],
            )
        )

        # Task 19.2: the API is the only caller of the agent runtime.
        if agent_runtime_arn is not None:
            self.instance_role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeAnalyticsAgent",
                    actions=["bedrock-agentcore:InvokeAgentRuntime"],
                    resources=[agent_runtime_arn, f"{agent_runtime_arn}/*"],
                )
            )

        self.access_role = iam.Role(
            self,
            "ApiAccessRole",
            assumed_by=iam.ServicePrincipal("build.apprunner.amazonaws.com"),
            description="Lets App Runner pull the API image from this stack's own ECR repo",
        )
        self.repository.grant_pull(self.access_role)

        # `deploy_api_service` (Task 18.1) lets the ECR repository and its
        # IAM roles be created and deployed *before* the App Runner service
        # references an image tag in that repository. Without this split, a
        # first-ever deploy fails at ApiService creation (no ":latest" image
        # pushed yet), which rolls the whole stack back to
        # ROLLBACK_COMPLETE; CDK then deletes and recreates the stack on
        # retry, which mints a brand-new (auto-named) ECR repository and
        # orphans the one an operator may have already pushed an image to.
        # Deploying in two passes -- once with this False, push the image,
        # then again with the default True -- keeps it a plain, safe
        # CloudFormation UPDATE the second time.
        self.api_service: apprunner.CfnService | None = None
        if deploy_api_service:
            self.api_service = apprunner.CfnService(
                self,
                "ApiService",
                source_configuration=apprunner.CfnService.SourceConfigurationProperty(
                    authentication_configuration=apprunner.CfnService.AuthenticationConfigurationProperty(
                        access_role_arn=self.access_role.role_arn
                    ),
                    auto_deployments_enabled=False,
                    image_repository=apprunner.CfnService.ImageRepositoryProperty(
                        image_identifier=f"{self.repository.repository_uri}:latest",
                        image_repository_type="ECR",
                        image_configuration=apprunner.CfnService.ImageConfigurationProperty(
                            port="8080",
                            runtime_environment_variables=[
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="XC_SNAPSHOT_BUCKET", value=data_bucket_name
                                ),
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="AWS_REGION", value=self.region
                                ),
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="SECRET_BACKEND", value="ssm"
                                ),
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="XC_PLATFORM_SSM_PREFIX", value="/xc-platform/"
                                ),
                                # Neither of these depends on the app
                                # client, so both are safe here regardless
                                # of ApiService/AppClient creation order --
                                # unlike XC_COGNITO_CLIENT_ID (SSM-sourced
                                # at runtime instead, see AppClient below):
                                # a client's callback_urls must reference
                                # this very service's URL, and CloudFormation
                                # does not allow two resources' properties
                                # to reference each other's attributes in
                                # both directions.
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="XC_COGNITO_USER_POOL_ID",
                                    value=self.user_pool.user_pool_id,
                                ),
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="XC_COGNITO_DOMAIN", value=self.cognito_domain_host
                                ),
                                # Also safe regardless of creation order --
                                # the frontend distribution has no
                                # dependency on ApiService, unlike the
                                # AppClient case above. Read by
                                # run_production_api.py, used by
                                # auth.py's callback() to redirect a real
                                # browser back to the app instead of
                                # returning raw JSON (Task 13/14).
                                apprunner.CfnService.KeyValuePairProperty(
                                    name="XC_FRONTEND_ORIGIN",
                                    value=f"https://{self.distribution.distribution_domain_name}",
                                ),
                                *(
                                    [
                                        apprunner.CfnService.KeyValuePairProperty(
                                            name="XC_AGENT_RUNTIME_ARN",
                                            value=agent_runtime_arn,
                                        )
                                    ]
                                    if agent_runtime_arn is not None
                                    else []
                                ),
                            ],
                        ),
                    ),
                ),
                instance_configuration=apprunner.CfnService.InstanceConfigurationProperty(
                    cpu="0.25 vCPU",
                    memory="0.5 GB",
                    instance_role_arn=self.instance_role.role_arn,
                ),
                health_check_configuration=apprunner.CfnService.HealthCheckConfigurationProperty(
                    protocol="HTTP",
                    path="/api/v1/system/health",
                    interval=10,
                    timeout=5,
                    healthy_threshold=1,
                    unhealthy_threshold=3,
                ),
            )

        # --- App client, created *after* ApiService specifically so its
        # OAuth callback/logout URLs can reference the service's real URL
        # (Task 18.2's real Cognito hosted-UI login). Before ApiService
        # exists (`deploy_api_service=False`'s phase-1 deploy), a
        # syntactically valid placeholder is used instead -- Cognito
        # requires at least one non-empty callback URL whenever the
        # authorization-code grant is enabled, but never checks it's
        # actually reachable at creation time. `XC_COGNITO_CLIENT_ID`
        # (needed by the app to build the same URLs from the other side) is
        # deliberately NOT an env var here -- an operator sets it in SSM
        # once, after this client exists, the same one-time step already
        # used for the ticket-signing secrets (docs/operations-and-
        # acceptance-readiness.md).
        api_base_url = (
            f"https://{self.api_service.attr_service_url}"
            if self.api_service is not None
            else "https://placeholder.invalid"
        )
        self.user_pool_client = self.user_pool.add_client(
            "AppClient",
            generate_secret=False,  # a public client; the API is the confidential party
            auth_flows=cognito.AuthFlow(user_password=True, admin_user_password=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[f"{api_base_url}/api/v1/auth/callback"],
                logout_urls=[f"{api_base_url}/"],
            ),
        )

        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        CfnOutput(self, "CognitoDomain", value=self.cognito_domain_host)
        CfnOutput(self, "EcrRepositoryUri", value=self.repository.repository_uri)
        if self.api_service is not None:
            CfnOutput(self, "ApiServiceArn", value=self.api_service.attr_service_arn)
            CfnOutput(self, "ApiServiceUrl", value=self.api_service.attr_service_url)
        CfnOutput(self, "FrontendUrl", value=self.distribution.distribution_domain_name)
