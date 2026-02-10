from aws_cdk import (
    Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elb,
    # aws_ecs_patterns as ecs_patterns,
    aws_secretsmanager as asm,
    aws_elasticache as el,
    aws_iam as iam,
    CfnOutput,
)
import aws_cdk
from constructs import Construct
from cdklab.ecs_component import EcsComponents
from cdklab.rds_component import RDSComponent

class LabDeployStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, app_config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # stack
        stack = aws_cdk.Stack.of(self)
        
        # vpc = ec2.Vpc(self, "labvpc", max_azs=3)     # default is all AZs in region
        vpc = ec2.Vpc(self, "labvpc",
            max_azs=2,  # Multi-AZ for high availability
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, # Private subnets with NAT for outbound internet
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, # Isolated subnets for RDS (no internet access)
                    cidr_mask=24
                )
            ]
        )

        # # domain name
        # domain_name = f'{config.get("cdklab").get("hostname")}.{config.get("dns").get("hostname_suffix", "")}.{hosted_zone.zone_name}'


        # elasticache redis
        self.redis_security_group = ec2.SecurityGroup(
            self,
            'redis_sg',
            vpc=vpc,
            allow_all_outbound=True
        )

        subnet_group = el.CfnSubnetGroup(
            self,
            'cdklab-redis-subnets',
            subnet_ids=[sub.subnet_id for sub in vpc.isolated_subnets],
            cache_subnet_group_name="cdklab",
            description="cdklab message queue"
        )

        self.redis = el.CfnReplicationGroup(
            self,
            'cdklab-redis',
            replication_group_id=f"{stack.stack_name}-redis",
            replication_group_description="cdklab redis",
            cache_node_type=app_config['cdklab'].get('redis_instance_type', 'cache.t4g.micro'),
            cache_parameter_group_name="default.redis7",
            engine="redis",
            engine_version="7.0",
            cache_subnet_group_name=subnet_group.cache_subnet_group_name,
            security_group_ids=[self.redis_security_group.security_group_id],
            transit_encryption_enabled=True,
            transit_encryption_mode="required",
            cluster_mode="disabled",
            num_cache_clusters=1,
            automatic_failover_enabled=False,
            auto_minor_version_upgrade=False,
        )

        self.redis.add_dependency(subnet_group)


        # ECS roles
        self.ecs_task_role = iam.Role(
            self,
            'ecs-task-role',
            assumed_by=iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AmazonECSTaskExecutionRolePolicy'),
                iam.ManagedPolicy.from_aws_managed_policy_name('AWSXrayFullAccess'),
            ],
            inline_policies={
                "output_telemetry": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "kms:CreateGrant",
                                "kms:RetireGrant",
                                "kms:DescribeKey",
                                "sts:AssumeRole"
                            ],
                            resources=["*"]
                        )
                    ]
                )
            }
        )

        # create database
        self.postgres = RDSComponent(
            self,
            "database",
            config=app_config["cdklab"]["database"],
            vpc=vpc,
            ecs_task_role=self.ecs_task_role
        )

       
        # common secrets across container created externally
        common_secret_map = {}
        for item in app_config['cdklab']['common']['environment'].get('secret', []):
            secret = asm.Secret.from_secret_complete_arn(self, f"secret-{item['name']}", secret_complete_arn=item['arn'])
            secret.grant_read(self.ecs_task_role)
            for key, path in item['mapping'].items():
                common_secret_map[key] = ecs.Secret.from_secrets_manager(secret, field=path)

        # common 
        common_env_map = app_config['cdklab']['common']['environment'].get('plaintext', {})

        # Add redis URL to comman environment variable map
        common_env_map["CELERY_BROKER_URL"] = f"rediss://{self.redis.attr_primary_end_point_address}:{self.redis.attr_primary_end_point_port}/0?ssl_cert_reqs=required"
        common_env_map["CELERY_RESULT_BACKEND"] = common_env_map["CELERY_BROKER_URL"]

        # lab repos
        image_repo = ecr.Repository.from_repository_arn(self, 'ecr', app_config['cdklab']['ecr'])

        # ecs cluster
        self.cluster = ecs.Cluster(
            self,
            'ecs',
            vpc=vpc
        )

        # add ecs dependency on the database being provisioned
        self.cluster.node.add_dependency(self.postgres.database)

        # service
        self.fastapi = EcsComponents(self,
            "fastapi",
            config=app_config["cdklab"]["fastapi"],
            image_repo=image_repo,
            vpc=vpc,
            ecs_task_role=self.ecs_task_role,
            database=self.postgres.database,
            cluster=self.cluster,
            alb=True,
            container_port=app_config["cdklab"]["fastapi"]["container_port"],
            health_check_path=app_config["cdklab"]["fastapi"]["health_check_path"],
            secrets_map=common_secret_map | self.postgres.secret_map,
            env_map=common_env_map | self.postgres.plaintext_env_map,
            redis_security_group=self.redis_security_group,
        )

        # celery worker
        self.celery_task = EcsComponents(self,
            "celery",
            config=app_config["cdklab"]["celery"],
            image_repo=image_repo,
            vpc=vpc,
            ecs_task_role=self.ecs_task_role,
            database=self.postgres.database,
            cluster=self.cluster,
            alb=False,
            secrets_map=common_secret_map | self.postgres.secret_map,
            env_map=common_env_map | self.postgres.plaintext_env_map,
            redis_security_group=self.redis_security_group,
        )

        # flower
        self.flower = EcsComponents(self,
            "flower",
            config=app_config["cdklab"]["flower"],
            image_repo=image_repo,
            vpc=vpc,
            ecs_task_role=self.ecs_task_role,
            cluster=self.cluster,
            alb=True,
            container_port=app_config["cdklab"]["flower"]["container_port"],
            health_check_path=app_config["cdklab"]["flower"]["health_check_path"],
            secrets_map=common_secret_map | self.postgres.secret_map,
            env_map=common_env_map | self.postgres.plaintext_env_map,
            redis_security_group=self.redis_security_group,
        )

        # # set certificate
        # self.cert = acm.Certificate(
        #     self,
        #     'cert',
        #     domain_name=domain_name,
        #     validation=acm.CertificateValidation.from_dns(hosted_zone)
        # )

        # create application load balancer containers
        self.lb = elb.ApplicationLoadBalancer(
            self, 
            "lb",
            vpc=vpc,
            internet_facing=True,
        )

        # create alb listner 
        # self.lb_listner = self.lb.add_listener(
        #     "listner",
        #     protocol=elb.ApplicationProtocol.HTTPS,
        #     open=True,
        #     certificates=[self.cert],
        #     default_target_groups=[self.flower.target_group]
        # )
        self.lb_listner = self.lb.add_listener(
            "listner",
            protocol=elb.ApplicationProtocol.HTTP,
            open=True,
            default_target_groups=[self.flower.target_group]
        )

        # add service path pattern action rule
        self.lb_listner.add_action(
            "listner_action",
            priority=10,
            conditions=[
                elb.ListenerCondition.path_patterns(app_config["cdklab"]["fastapi"]["path_patterns"])
            ],
            action=elb.ListenerAction.forward(
                target_groups=[self.fastapi.target_group]
            )
        )

        # # add CNAME entry to route53
        # self.dns_entry = route53.ARecord(
        #     self,
        #     "dns",
        #     zone=hosted_zone,
        #     record_name=domain_name,
        #     target=route53.RecordTarget.from_alias(r53t.LoadBalancerTarget(self.lb))
        # )

        CfnOutput(
            self, "LoadBalancerDNS",
            value=self.lb.load_balancer.load_balancer_dns_name,
            description="The DNS name of the load balancer"
        )

