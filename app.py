#!/usr/bin/env python3
import os
import aws_cdk as cdk
from yaml import load, CLoader as Loader
from cdklab.cdklab_stack import LabDeployStack


app = cdk.App()
app_config = load(open("config/config.yaml", 'r'), Loader=Loader)
LabDeployStack(
    app,
    "LabStack",
    app_config,    
    env=cdk.Environment(account=f"{app_config['account']['id']}", region=f"{app_config['account']['region']}")
)

app.synth()
