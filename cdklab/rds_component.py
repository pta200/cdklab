import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecs as ecs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_rds as rds
import constructs


class RDSComponent(constructs.Construct):
    def __init__(
            self,
            scope: constructs.Construct,
            construct_id: str,
            *,
            vpc: ec2.IVpc,
            ecs_task_role: iam.Role,
            config: dict,
            **kwargs
    ) -> None:
        super().__init__(scope, construct_id)

        # RDS Postgres database
        self.db_security_group = ec2.SecurityGroup(
            self,
            'db-sg',
            vpc=vpc,
            allow_all_outbound=True
        )

        self.database = rds.DatabaseCluster(
            self,
            'db',
            cluster_identifier=config.get("database_name"),
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_8),
            storage_encrypted=True,
            credentials=rds.Credentials.from_generated_secret(config.get("database_name")),
            default_database_name=config.get("database_name"),
            serverless_v2_max_capacity=config.get("db_max_acu", 16),
            serverless_v2_min_capacity=config.get("db_min_acu", 0.5),
            vpc=vpc,
            backup=rds.BackupProps(
                retention=cdk.Duration.days(7),
            ),
            enable_performance_insights=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            writer=rds.ClusterInstance.serverless_v2(
                    'writer',
                    publicly_accessible=False,
                    auto_minor_version_upgrade=config.get('auto_minor_upgrades', False),
                )
        )
        self.database.secret.grant_read(ecs_task_role)

        # Add the db secret to the list
        self.secret_map = {}
        self.secret_map['DB_USERNAME'] = ecs.Secret.from_secrets_manager(self.database.secret, field='username')
        self.secret_map['DB_PASSWORD'] = ecs.Secret.from_secrets_manager(self.database.secret, field='password')

        self.plaintext_env_map = {}
        self.plaintext_env_map["DB_HOST"] = self.database.cluster_endpoint.hostname
        self.plaintext_env_map["DB_PORT"] = cdk.Token.as_string(self.database.cluster_endpoint.port)
        self.plaintext_env_map["DB_NAME"] = config.get("database_name")
