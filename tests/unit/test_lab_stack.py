import aws_cdk as core
import aws_cdk.assertions as assertions

from cdklab.cdklab_stack import LabDeployStack

# example tests. To run these tests, uncomment this file along with the example
def test_sqs_queue_created():
    app = core.App()
    stack = LabDeployStack(app, "labstack")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
