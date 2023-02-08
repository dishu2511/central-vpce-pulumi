import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions
import json

# defining config file
with open("./../config.json") as config_file:
    data = json.load(config_file)


SPOKE_CIDR_BLOCK = data["SPOKE_CIDR_BLOCK"]
STACK_NAME = data["SPOKE_VPC_STACK"]

SPOKE_ACCOUNT_ID = data["SPOKE_ACCOUNT_ID"]
NETWORKHUB_ACCOUNT_ID = data["NETWORKHUB_ACCOUNT_ID"]
ROLE_NAME = data["ROLE_NAME"]
REGION = data["REGION"]


def create_vpc(name):
    # creating provider for networkhub account
    networkhub_provider = aws.Provider(
        f"provider-{NETWORKHUB_ACCOUNT_ID}",
        region=REGION,
        assume_role=aws.ProviderAssumeRoleArgs(
            role_arn=f"arn:aws:iam::{NETWORKHUB_ACCOUNT_ID}:role/{ROLE_NAME}",
        ),
    )
    # creating vpc
    vpc = aws.ec2.Vpc(
        name,
        cidr_block=SPOKE_CIDR_BLOCK,
        enable_dns_support=True,
        enable_dns_hostnames=True,
        tags={
            "Name": name,
        },
    )
    pulumi.export("vpc_id", vpc.id)

    # exporting the vpc id and storing it in networkhub account
    vpc_ssm = aws.ssm.Parameter(
        f"{name}-vpc-id",
        type="String",
        name=f"{name}-vpc-id",
        value=vpc.id,
        opts=ResourceOptions(
            provider=networkhub_provider,
        ),
    )
    # # creating internet gateway
    # igw = aws.ec2.InternetGateway(
    #     f"{name}-igw",
    #     vpc_id=vpc.id,
    #     tags={
    #         "Name": f"{name}-igw",
    #     },
    # )

    # getting availability zones
    availability_zones = aws.get_availability_zones(state="available")

    # creating 3 public subnets
    public_subnets = []
    private_subnets = []
    db_subnets = []
    private_subnet_ids = []

    for i in range(3):
        public_subnet = aws.ec2.Subnet(
            f"{name}-public-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.1.{i}.0/24",
            availability_zone=availability_zones.names[int(i)],
            map_public_ip_on_launch=True,
            tags={
                "Name": f"{name}-publicsubnet-{availability_zones.names[int(i)]}",
            },
        )
        public_subnets.append(public_subnet)

        # creating 3 private subnets
        private_subnet = aws.ec2.Subnet(
            f"{name}-private-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.1.{i+3}.0/24",
            availability_zone=availability_zones.names[int(i)],
            map_public_ip_on_launch=False,
            tags={
                "Name": f"{name}-privatesubnet-{availability_zones.names[int(i)]}",
            },
        )
        private_subnets.append(private_subnet)

        # creating 3 db subnets
        db_subnet = aws.ec2.Subnet(
            f"{name}-db-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.1.{i+6}.0/24",
            availability_zone=availability_zones.names[int(i)],
            map_public_ip_on_launch=True,
            tags={
                "Name": f"{name}-dbsubnet-{availability_zones.names[int(i)]}",
            },
        )
        db_subnets.append(db_subnet)

    private_route_table = aws.ec2.RouteTable(
        f"{name}-private-route-table",
        vpc_id=vpc.id,
        # routes=[
        #     aws.ec2.RouteTableRouteArgs(
        #         cidr_block="0.0.0.0/0",
        #         transit_gateway_id=tgw.id,
        #     )
        # ],
        tags={
            "Name": f"{name}-private-route-table",
        },
        # opts=pulumi.ResourceOptions(depends_on=[tgw]),
    )

    # creating public route table
    public_route_table = aws.ec2.RouteTable(
        f"{name}-public-route-table",
        vpc_id=vpc.id,
        # routes=[
        #     aws.ec2.RouteTableRouteArgs(
        #         cidr_block="10.3.0.0/16",
        #         gateway_id=igw.id,
        #     )
        # ],
        tags={
            "Name": f"{name}-public-route-table",
        },
    )

    def route_table_association(subnet, route_table, type):
        aws.ec2.RouteTableAssociation(
            f"{name}-{type}-route-table-asso-{availability_zones.names[int(i)]}-{i}",
            subnet_id=subnet,
            route_table_id=route_table,
        )

    for i, subnet in zip(range(3), private_subnets):
        route_table_association(subnet, private_route_table, "public")
    for i, subnet in zip(range(3), public_subnets):
        route_table_association(subnet, public_route_table, "private")

    def nacl(subnets, type):
        aws.ec2.NetworkAcl(
            f"{name}-nacl-{type}",
            vpc_id=vpc.id,
            subnet_ids=subnets,
            egress=[
                aws.ec2.NetworkAclEgressArgs(
                    protocol="-1",
                    rule_no=200,
                    action="allow",
                    cidr_block="0.0.0.0/0",
                    from_port=0,
                    to_port=0,
                )
            ],
            ingress=[
                aws.ec2.NetworkAclIngressArgs(
                    protocol="-1",
                    rule_no=100,
                    action="allow",
                    cidr_block="0.0.0.0/0",
                    from_port=0,
                    to_port=0,
                )
            ],
            tags={
                "Name": f"{name}-nacl-public",
            },
        )

    tgw = aws.ec2transitgateway.get_transit_gateway(
        filters=[
            aws.ec2transitgateway.GetTransitGatewayFilterArgs(
                name="options.amazon-side-asn",
                values=["64512"],
            )
        ]
    )
    vpc_tgw_attachment = aws.ec2transitgateway.VpcAttachment(
        f"{name}-tgw-attachment",
        subnet_ids=private_subnets,
        transit_gateway_id=tgw.id,
        vpc_id=vpc.id,
        tags={
            "Name": f"{name}-tgw-attachment",
        },
    )

    # # adding tgw route to the private route table

    private_route_spoke = aws.ec2.Route(
        f"{name}-private-route table-tgw-route",
        destination_cidr_block="0.0.0.0/0",
        transit_gateway_id=tgw.id,
        route_table_id=private_route_table.id,
        opts=pulumi.ResourceOptions(depends_on=[vpc_tgw_attachment]),
    )

    public_route_spoke = aws.ec2.Route(
        f"{name}-public-route table-tgw-route",
        destination_cidr_block="0.0.0.0/0",
        transit_gateway_id=tgw.id,
        route_table_id=public_route_table.id,
        opts=pulumi.ResourceOptions(depends_on=[vpc_tgw_attachment]),
    )

    nacl(public_subnets, "public")
    nacl(private_subnets, "private")

    # return pulumi.Output.from_input(vpc.id)
    return vpc


# stack = pulumi.StackReference(pulumi.get_stack())
create_vpc(STACK_NAME)
