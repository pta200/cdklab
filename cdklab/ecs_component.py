from aws_cdk import (
    aws_route53 as route53,
    aws_route53_targets as r53t,
    aws_certificatemanager as acm,
    aws_ecs_patterns as ecs_patterns,
    aws_secretsmanager as asm,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elb,
    aws_iam as iam,
    aws_elasticache as ec,
    aws_lambda as lambda_,
    custom_resources as custom,
    aws_rds as rds,
    CfnOutput,
    Stack,
    Duration
)
import aws_cdk as cdk
import constructs

class EcsComponents(constructs.Construct):
    def __init__(
            self,
            scope: constructs.Construct,
            construct_id: str,
            *,
            config: dict,
            image_repo,
            alb: bool,
            vpc: ec2.Vpc,
            cluster: ecs.Cluster, 
            health_check_path: str = None,
            container_port: int = None,
            secrets_map: dict = None,
            env_map: dict = None,
            ecs_task_role: iam.Role,
            database: rds.DatabaseCluster = None,
            redis_security_group: ec2.SecurityGroup = None,
            **kwargs
    ):
        super().__init__(scope, construct_id)

        stack = cdk.Stack.of(self)

        # Some secrets are created externally, e.g. LDAP creds
        comp_secret_map = {}
        for item in config['environment'].get('secret', []) or []:
            secret = asm.Secret.from_secret_complete_arn(self, f"secret-{item['name']}", secret_complete_arn=item['arn'])
            secret.grant_read(ecs_task_role)
            for key, path in item['mapping'].items():
                comp_secret_map[key] = ecs.Secret.from_secrets_manager(secret, field=path)

        if secrets_map:
            comp_secret_map.update(secrets_map)

        # Add plaintext variables and the cache url
        comp_env_map = config['environment'].get('plaintext') or {}
        if env_map:
            comp_env_map.update(env_map)

        # fargate task config
        self.task = ecs.FargateTaskDefinition(
            self,
            f'{construct_id}-task',
            family=f'{construct_id}',
            execution_role=ecs_task_role,
            memory_limit_mib=config.get('memory_limit', 1024),
            cpu=config.get('cpu_limit', 512),
        )

        # check if container has a start command to pass as an argument
        command = None
        if config.get("start_command"):
            command = config.get("start_command")

        # define container
        self.container = self.task.add_container(
            f'{construct_id}-container',
            image=ecs.ContainerImage.from_ecr_repository(image_repo, config.get("image")),
            logging=ecs.LogDriver.aws_logs(
                log_group=logs.LogGroup(
                    self,
                    f'{construct_id}-log-group',
                    log_group_name=f"/{stack.stack_name}/ecs/jobscheduler/{construct_id}",
                    retention=logs.RetentionDays.ONE_WEEK,
                    removal_policy=cdk.RemovalPolicy.DESTROY
                ),
                stream_prefix='container'
            ),
            environment=comp_env_map,
            command=command,
            secrets=comp_secret_map
        )

        # assign port for inbound access
        if container_port:
            self.container.add_port_mappings(ecs.PortMapping(container_port=container_port))

        # define service
        self.service = ecs.FargateService(
            self,
            f'{construct_id}-service',
            cluster=cluster,
            task_definition=self.task ,
            platform_version=ecs.FargatePlatformVersion.VERSION1_4,
            assign_public_ip=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(
                enable=True,
                rollback=True,
            )
        )

        # allow access to redis
        if redis_security_group:
            for security_group in self.service.connections.security_groups:
                redis_security_group.connections.allow_from(
                    security_group,
                    ec2.Port.tcp(6379),
                    'service redis connection [CDK]'
                )

        # add container access to the database endpoint
        if database:    
            for security_group in self.service.connections.security_groups:
                database.connections.allow_from(
                    security_group,
                    ec2.Port.tcp(database.cluster_endpoint.port),
                    'ECS task connection [CDK]'
                )
            # add dependency on the database being up for the ecs service to start a container
            # self.service.node.add_dependency(database)

        # if container requires application load balancer setup health check and target group
        if alb:
             # create ALB health checks
            self.health_check = elb.HealthCheck(
                interval=Duration.seconds(60),
                path=health_check_path,
                timeout=Duration.seconds(5),
                healthy_http_codes='200',
                healthy_threshold_count=3,
                unhealthy_threshold_count=2
            )

            # create ALB target groups
            self.target_group = elb.ApplicationTargetGroup(
                self,
                f"{construct_id}-target_group",
                health_check=self.health_check,
                port=container_port,
                protocol=elb.ApplicationProtocol.HTTP,
                target_type=elb.TargetType.IP,
                vpc=vpc,
                targets=[self.service],
            )
