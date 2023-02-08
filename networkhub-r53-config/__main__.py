import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions
import json

# defining config file
with open("./../config.json") as config_file:
    data = json.load(config_file)

NETWORKHUB_VPC_STACK = data["NETWORKHUB_VPC_STACK"]
SPOKE_VPC_STACK = data["SPOKE_VPC_STACK"]

NETWORKHUB_CIDR_BLOCK = data["NETWORKHUB_CIDR_BLOCK"]

EC2_MESSAGES_ENDPOINT = data["EC2_MESSAGES_ENDPOINT"]
SSM_MESSAGES_ENDPOINT = data["SSM_MESSAGES_ENDPOINT"]
SSM_ENDPOINT = data["SSM_ENDPOINT"]

SPOKE_ACCOUNT_ID = data["SPOKE_ACCOUNT_ID"]
ROLE_NAME = data["ROLE_NAME"]
REGION = data["REGION"]

# creating provider for spoke account
spoke_provider = aws.Provider(
    f"provider-{SPOKE_ACCOUNT_ID}-access",
    region=REGION,
    assume_role=aws.ProviderAssumeRoleArgs(
        role_arn=f"arn:aws:iam::{SPOKE_ACCOUNT_ID}:role/{ROLE_NAME}",
    ),
)


def route53_private_hosted_zone_config(service, endpoint):
    networkhub_vpc = aws.ssm.get_parameter(name=f"{NETWORKHUB_VPC_STACK}-vpc-id")
    spoke_vpc = aws.ssm.get_parameter(name=f"{SPOKE_VPC_STACK}-vpc-id")
    vpce_endpoint = aws.ec2.get_vpc_endpoint(
        vpc_id=networkhub_vpc.value,
        service_name=f"com.amazonaws.{REGION}.{service}",
    )

    hosted_zone = aws.route53.Zone(
        f"{service}-private-hosted-zone",
        vpcs=[
            aws.route53.ZoneVpcArgs(
                vpc_id=networkhub_vpc.value,
            )
        ],
        name=endpoint,
    )
    hosted_zone_record = aws.route53.Record(
        f"{service}-private-hosted-zone-record",
        zone_id=hosted_zone.id,
        name=endpoint,
        type="A",
        aliases=[
            aws.route53.RecordAliasArgs(
                name=vpce_endpoint.dns_entries[0].get("dns_name"),
                zone_id="ZDK2GCRPAFKGO",
                evaluate_target_health=True,
            )
        ],
    )

    spoke_vpc_association_authorization = aws.route53.VpcAssociationAuthorization(
        f"{service}-vpc_association-auth",
        vpc_id=spoke_vpc.value,
        zone_id=hosted_zone.id,
    )

    spoke_zone_association = aws.route53.ZoneAssociation(
        f"{service}-zone-ssociation",
        vpc_id=spoke_vpc.value,
        zone_id=hosted_zone.id,
        opts=pulumi.ResourceOptions(
            provider=spoke_provider, depends_on=[spoke_vpc_association_authorization]
        ),
    )


route53_private_hosted_zone_config("ssm", SSM_ENDPOINT)
route53_private_hosted_zone_config("ssmmessages", SSM_MESSAGES_ENDPOINT)
route53_private_hosted_zone_config("ec2messages", EC2_MESSAGES_ENDPOINT)
