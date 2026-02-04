from aws_cdk import (
    # Duration,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    CfnOutput,
    # aws_sqs as sqs,
)
from constructs import Construct

class LabDeployStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, app_config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # vpc = ec2.Vpc(self, "labtvpc", max_azs=3)     # default is all AZs in region
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

        cluster = ecs.Cluster(self, "labcluster", vpc=vpc)

        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(self, "lab",
            cluster=cluster,            # Required
            cpu=512,                    # Default is 256
            desired_count=1,            # Default is 1
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_registry("amazon/amazon-ecs-sample")),
            memory_limit_mib=1024,      # Default is 512
            public_load_balancer=True)  # Default is True
        
        CfnOutput(
            self, "LoadBalancerDNS",
            value=fargate_service.load_balancer.load_balancer_dns_name,
            description="The DNS name of the load balancer"
        )
