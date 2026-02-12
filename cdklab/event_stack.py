import aws_cdk as cdk
from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_glue as glue,
    aws_iam as iam,
    aws_kinesis as kinesis,
    aws_kinesisfirehose as firehose,
    aws_lambda as aws_lambda,
    aws_s3 as s3,
    aws_logs as logs,
)
from constructs import Construct
from cdklab.lambda_deploy import LambdaDeploy


class AnalyticsDeployStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, vpc, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)


        # Bucket for config and storing code archives
        self.bucket = s3.Bucket(
            self,
            "bucket",
            bucket_name=config['cdklab']["bucket_name"],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN_ON_UPDATE_OR_DELETE,
            encryption=s3.BucketEncryption.S3_MANAGED,
            event_bridge_enabled=False,
            versioned=False,
            bucket_key_enabled=True,
        )

        # glue database
        self.glue_db = glue.CfnDatabase(
            self,
            "glue",
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=config["cdklab"]["glue_dbname"],
            ),
            catalog_id=self.account,
        )

        self.glue_table = glue.CfnTable(
            self, "gluetable",
            catalog_id=self.account,
            database_name=self.glue_db.database_input.name,
            table_input=glue.CfnTable.TableInputProperty(
                name="app_events",
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[
                        glue.CfnTable.ColumnProperty(name="app_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="event_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="event_type", type="string"),
                        glue.CfnTable.ColumnProperty(name="event_uri", type="string"),
                        glue.CfnTable.ColumnProperty(name="user_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="session_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="attributes", type="struct<action:string,duration:int,status:string>"),
                        glue.CfnTable.ColumnProperty(name="device", type="struct<hostname:string,os:string,client_ip:string>"),
                        glue.CfnTable.ColumnProperty(name="createts", type="timestamp"),
                    ],
                    input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                        parameters={
                            "serialization.format": "1"
                        }
                    ),
                    location=f"s3://{self.bucket.bucket_name}/{config['analytics']['firehose_stream_prefix']}", # This is a placeholder, Firehose will write here
                ),
                table_type="EXTERNAL_TABLE",
            ),
        )


        self.glue_role = iam.Role(
            self,
            "gluerole",
            max_session_duration=Duration.seconds(3600),
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                ),
            ],
            inline_policies={
                "assume_role": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["sts:AssumeRole"],
                            resources=["*"],
                        ),
                    ]
                ),
                "bucket": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["s3:GetObject", "s3:PutObject"],
                            resources=[self.bucket.bucket_arn],
                        ),
                    ]
                ),
            },
        )

        # stream
        self.stream = kinesis.CfnStream(
            self,
            "stream",
            retention_period_hours=24,
            name=config["cdklab"]["kinesis_stream"],
            shard_count=4,
            stream_encryption=kinesis.CfnStream.StreamEncryptionProperty(
                encryption_type="KMS", key_id="alias/aws/kinesis"
            ),
        )

        # create API gateway and deploy lambdas
        self.lambda_deploy = LambdaDeploy(
            self,
            "events",
            vpc=vpc,
            stream=self.stream,
        )

        # firehose role
        self.firehose_log_group = logs.LogGroup(
            self, "FirehoseLogGroup",
            log_group_name=f'/aws/kinesisfirehose/{config["cdklab"]["firehose_stream_name"]}',
            retention=logs.RetentionDays.ONE_WEEK  # Adjust retention as needed
        )

        self.fh_role = iam.Role(
            self,
            "fhrole",
            max_session_duration=Duration.seconds(3600),
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonKinesisFirehoseFullAccess"
                ),
            ],
            inline_policies={
                "assume_role": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["sts:AssumeRole"],
                            resources=["*"],
                        ),
                    ]
                ),
                "logs": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "logs:PutLogEvents",
                                "logs:CreateLogStream"
                            ],
                            resources=[self.firehose_log_group.log_group_arn],
                        ),
                    ]
                ),
                "kenesis": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "kinesis:DescribeStream",
                                "kinesis:GetShardIterator",
                                "kinesis:GetRecords",
                                "kinesis:ListShards",
                            ],
                            resources=[self.stream.attr_arn],
                        ),
                    ]
                ),
                "glue": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "glue:GetTableVersions",
                                "glue:GetTable",
                                "glue:GetTableVersion",
                            ],
                            resources=[
                                f"arn:aws:glue:{self.region}:{self.account}:catalog",
                                f"arn:aws:glue:{self.region}:{self.account}:database/{self.glue_db.database_input.name}",
                                f"arn:aws:glue:{self.region}:{self.account}:table/{self.glue_db.database_input.name}/{self.glue_table.table_input.name}",
                            ],
                        ),
                    ]
                ),
                "bucket": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=[
                                "s3:AbortMultipartUpload",
                                "s3:GetBucketLocation",
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:ListBucketMultipartUploads",
                                "s3:PutObject",
                            ],
                            resources=[
                                self.bucket.bucket_arn,
                                f"{self.bucket.bucket_arn}/*",
                            ],
                        ),
                    ]
                ),
            },
        )

        self.fh_delivery_stream = firehose.CfnDeliveryStream(
            self,
            "fhstream",
            delivery_stream_name=config["cdklab"]["firehose_stream_name"],
            delivery_stream_type="KinesisStreamAsSource",
            kinesis_stream_source_configuration=firehose.CfnDeliveryStream.KinesisStreamSourceConfigurationProperty(
                kinesis_stream_arn=self.stream.attr_arn, role_arn=self.fh_role.role_arn
            ),
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=self.bucket.bucket_arn,
                role_arn=self.fh_role.role_arn,
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    size_in_m_bs=64, # Minimum 64 MB when format conversion is enabled
                    interval_in_seconds=300
                ),
                prefix=f"{config['analytics']['firehose_stream_prefix']}/",
                # Compression is handled by ParquetSerDe, so set to UNCOMPRESSED
                compression_format="UNCOMPRESSED",
                data_format_conversion_configuration=firehose.CfnDeliveryStream.DataFormatConversionConfigurationProperty(
                    enabled=True,
                    input_format_configuration=firehose.CfnDeliveryStream.InputFormatConfigurationProperty(
                            deserializer=firehose.CfnDeliveryStream.DeserializerProperty(
                                open_x_json_ser_de=firehose.CfnDeliveryStream.OpenXJsonSerDeProperty(
                                    case_insensitive=False,
                                    convert_dots_in_json_keys_to_underscores=False
                                )
                        )
                    ),
                    output_format_configuration=firehose.CfnDeliveryStream.OutputFormatConfigurationProperty(
                        serializer=firehose.CfnDeliveryStream.SerializerProperty(
                            parquet_ser_de=firehose.CfnDeliveryStream.ParquetSerDeProperty(
                                compression="GZIP",
                            )
                        )
                    ),
                    schema_configuration=firehose.CfnDeliveryStream.SchemaConfigurationProperty(
                        catalog_id=self.account,
                        database_name=self.glue_db.database_input.name,
                        table_name=self.glue_table.table_input.name,
                        role_arn=self.fh_role.role_arn,
                    )
                ),
                cloud_watch_logging_options=firehose.CfnDeliveryStream.CloudWatchLoggingOptionsProperty(
                    enabled=True,
                    log_group_name=self.firehose_log_group.log_group_name,
                    log_stream_name="DestinationDelivery",
                ),
                encryption_configuration=firehose.CfnDeliveryStream.EncryptionConfigurationProperty(
                    no_encryption_config="NoEncryption"
                ),
            ),
        )

        self.fh_delivery_stream.add_dependency(self.bucket.node.default_child)

        self.glueCrawler = glue.CfnCrawler(
            self,
            "GlueCrawler",
            role=self.glue_role.role_arn,
            targets=glue.CfnCrawler.TargetsProperty(
                s3_targets=[
                    glue.CfnCrawler.S3TargetProperty(
                        path=f"{self.bucket.bucket_name}/",
                    )
                ]
            ),
            database_name=config["cdklab"]["glue_dbname"],
            schema_change_policy=glue.CfnCrawler.SchemaChangePolicyProperty(
                update_behavior="LOG",
                delete_behavior="LOG",
            ),
            configuration='{"Version":1.0,"Grouping":{"TableGroupingPolicy":"CombineCompatibleSchemas"}}',
        )

