import aws_cdk as cdk
import aws_cdk.aws_apigateway as apigateway
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_iam as iam
import aws_cdk.aws_lambda as lmb
import aws_cdk.aws_logs as logs
import aws_cdk.aws_kinesis as kinesis
import constructs


class LambdaDeploy(constructs.Construct):
    def __init__(
            self,
            scope: constructs.Construct,
            construct_id: str,
            *,
            vpc: ec2.Vpc,
            stream: kinesis.CfnStream,
            **kwargs
    ):
        super().__init__(scope, construct_id)

        stack = cdk.Stack.of(self)

        # gateway
        self.apigw_endpoint = vpc.add_interface_endpoint(
            'apigw',
            service=ec2.InterfaceVpcEndpointAwsService.APIGATEWAY,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            private_dns_enabled=False,
        )

        # REST API
        self.api = apigateway.RestApi(
            self,
            "restapi",
            rest_api_name="events",
            default_method_options=apigateway.MethodOptions(api_key_required=False),
            endpoint_configuration=apigateway.EndpointConfiguration(
                types=[apigateway.EndpointType.PUBLIC],
                vpc_endpoints=[self.apigw_endpoint]
            ),
            policy=iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        principals=[iam.AnyPrincipal()],
                        actions=['execute-api:Invoke'],
                        resources=['*'],
                        effect=iam.Effect.DENY,
                        conditions={
                            "StringNotEquals": {
                                "aws:SourceVpce": self.apigw_endpoint.vpc_endpoint_id
                            }
                        }
                    ),
                    iam.PolicyStatement(
                        principals=[iam.AnyPrincipal()],
                        actions=['execute-api:Invoke'],
                        resources=['*'],
                        effect=iam.Effect.ALLOW
                    )
                ]
            )
        )

        # lambda role including access to add data to Kinesis stream
        self.role = iam.Role(
            self,
            'role',
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
            ],
            inline_policies={
                "kinesis_writes": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="clickeventingestkenesis",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "kinesis:PutRecord",
                                "kinesis:PutRecords",
                                "kinesis:GetShardIterator",
                                "kinesis:GetRecords",
                                "kinesis:DescribeStream"
                            ],
                            resources=[stream.attr_arn]
                        )
                    ]
                ),
                "kinesis_reads": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            sid="clickeventingestkenesis",
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "kinesis:ListStreams",
                                "kinesis:ListShards"
                            ],
                            resources=["*"]
                        )
                    ]
                ),
            }
        )

        # output sg
        self.lambda_security_group = ec2.SecurityGroup(
            self,
            'sg',
            vpc=vpc,
            allow_all_outbound=True
        )

        self.v1_path = self.api.root.add_resource("v1",  default_method_options=apigateway.MethodOptions(api_key_required=False))

        self.func_events = lmb.Function(
            self,
            'fn',
            function_name=f"{stack.stack_name}-ingest",
            code=lmb.Code.from_asset(path=f'./lambda/ingest'),
            runtime=lmb.Runtime('python3.11'),
            handler="function.handler",
            role=self.role,
            memory_size=256,
            timeout=cdk.Duration.seconds(15),
            # vpc=vpc,
            # vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            # security_groups=[self.lambda_security_group],
            log_group=logs.LogGroup(
                self,
                "log_group",
                log_group_name=f"{stack.stack_name}-event",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=cdk.RemovalPolicy.DESTROY,
            ),
            environment={
                "KDS_NAME": stream.name
            },
            layers=[]
        )
        
            
        self.ingest_path = self.v1_path.add_resource("events")
        self.ingest_path.add_method(
            "POST",
            apigateway.LambdaIntegration(self.func_events),
            api_key_required=False
        )

        cdk.CfnOutput(
            self, "rest_path",
            value=self.api.url_for_path(f"/v1/events"),
            description=f"rest path"
        )

        cdk.CfnOutput(
            self,
            'GW URL',
            value=f"https://{self.api.rest_api_id}-{self.apigw_endpoint.vpc_endpoint_id}.execute-api.{stack.region}.amazonaws.com/prod"
        )
