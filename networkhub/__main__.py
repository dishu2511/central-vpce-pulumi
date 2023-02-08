import pulumi
import pulumi_aws as aws
import json

# defining config file
with open("./../config.json") as config_file:
    data = json.load(config_file)

NETWORKHUB_CIDR_BLOCK = data["NETWORKHUB_CIDR_BLOCK"]
SPOKE_CIDR_BLOCK = data["SPOKE_CIDR_BLOCK"]
VPCE_INBOUND_PORT = data["VPCE_INBOUND_PORT"]
STACK_NAME = data["NETWORKHUB_VPC_STACK"]
REGION = data["REGION"]

# main function to create networkhub resources


def create_vpc(name):

    # creating vpc
    vpc = aws.ec2.Vpc(
        name,
        cidr_block=NETWORKHUB_CIDR_BLOCK,
        enable_dns_support=True,
        enable_dns_hostnames=True,
        tags={
            "Name": name,
        },
    )
    pulumi.export("vpc_id", vpc.id)

    vpc_ssm = aws.ssm.Parameter(
        f"{name}-vpc-id", type="String", name=f"{name}-vpc-id", value=vpc.id
    )

    # creating internet gateway
    igw = aws.ec2.InternetGateway(
        f"{name}-igw",
        vpc_id=vpc.id,
        tags={
            "Name": f"{name}-igw",
        },
    )

    # getting availability zones
    availability_zones = aws.get_availability_zones(state="available")

    # declaring subnet lists
    public_subnets = []
    private_subnets = []
    private_subnet_ids = []

    for i in range(3):
        # creating 3 public subnets
        public_subnet = aws.ec2.Subnet(
            f"{name}-public-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.0.{i}.0/24",
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
            cidr_block=f"10.0.{i+3}.0/24",
            availability_zone=availability_zones.names[int(i)],
            map_public_ip_on_launch=False,
            tags={
                "Name": f"{name}-privatesubnet-{availability_zones.names[int(i)]}",
            },
        )
        private_subnets.append(private_subnet)

    # creating private route table
    private_route_table = aws.ec2.RouteTable(
        f"{name}-private-route-table",
        vpc_id=vpc.id,
        tags={
            "Name": f"{name}-private-route-table-{availability_zones.names[int(i)]}-{i}",
        },
    )

    # creating public route table
    public_route_table = aws.ec2.RouteTable(
        f"{name}-public-route-table",
        vpc_id=vpc.id,
        routes=[
            aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw.id,
            )
        ],
        tags={
            "Name": f"{name}-public-route-table-{availability_zones.names[int(i)]}-{i}",
        },
    )

    # function to create route table association
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

    # function to create Network ACLs
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

    # creating transit gateway
    tgw = aws.ec2transitgateway.TransitGateway(
        f"{name}-tgw",
        description=f"{name}-tgw",
        auto_accept_shared_attachments="enable",
        default_route_table_association="enable",
        default_route_table_propagation="enable",
        dns_support="enable",
        tags={
            "Name": f"{name}-tgw",
        },
    )

    # creating VPC attachment for TGW
    vpc_tgw_attachment = aws.ec2transitgateway.VpcAttachment(
        f"{name}-tgw-attachment",
        subnet_ids=private_subnets,
        transit_gateway_id=tgw.id,
        vpc_id=vpc.id,
        tags={
            "Name": f"{name}-tgw-attachment",
        },
    )

    # adding tgw route to the private route table
    private_route_dev2 = aws.ec2.Route(
        f"{name}-private-route table-tgw-route",
        destination_cidr_block="0.0.0.0/0",
        transit_gateway_id=tgw.id,
        route_table_id=private_route_table.id,
        opts=pulumi.ResourceOptions(depends_on=[vpc_tgw_attachment]),
    )

    # creating TGW share via RAM (Resource Access Manager)
    tgw_share_organization = aws.ram.ResourceShare(
        f"{name}-tgw-share-organization",
        allow_external_principals=True,
        tags={
            "Name": f"{name}-tgw-share-organization",
        },
    )
    ram_resource_association = aws.ram.ResourceAssociation(
        f"{name}-ram-resource-association",
        resource_arn=tgw.arn,
        resource_share_arn=tgw_share_organization.arn,
    )

    # getting organization ID
    org = aws.organizations.get_organization()
    share_principal_association = aws.ram.PrincipalAssociation(
        f"{name}-share-principal-association",
        principal=f"arn:aws:organizations::{org.master_account_id}:organization/{org.id}",
        resource_share_arn=tgw_share_organization.arn,
    )

    # function to create VPC endpoints
    def vpce(endpoint_type, service, service_name):
        if endpoint_type == "Interface":
            vpce_security_group = aws.ec2.SecurityGroup(
                f"{STACK_NAME}-vpce-{service}-security-group",
                name=f"{STACK_NAME}--{service}-vpce-security-group",
                vpc_id=vpc.id,
                ingress=[
                    aws.ec2.SecurityGroupIngressArgs(
                        cidr_blocks=[NETWORKHUB_CIDR_BLOCK, SPOKE_CIDR_BLOCK],
                        protocol="tcp",
                        from_port=VPCE_INBOUND_PORT,
                        to_port=VPCE_INBOUND_PORT,
                    )
                ],
                egress=[
                    aws.ec2.SecurityGroupEgressArgs(
                        from_port=0,
                        to_port=0,
                        protocol="-1",
                        cidr_blocks=["0.0.0.0/0"],
                    )
                ],
            )

            endpoint = aws.ec2.VpcEndpoint(
                f"{STACK_NAME}-vpce-{service}",
                # vpc_id=stack.get_output("vpc_id"),
                vpc_id=vpc.id,
                service_name=service_name,
                # subnet_ids=stack.get_output("private_subnet_ids"),
                subnet_ids=private_subnets,
                # subnet_ids=vpc_id,
                vpc_endpoint_type=endpoint_type,
                security_group_ids=[vpce_security_group.id],
                private_dns_enabled=False,
                tags={
                    "Name": f"{STACK_NAME}-vpce-{service}",
                },
            )
        elif endpoint_type == "Gateway":
            endpoint = aws.ec2.VpcEndpoint(
                f"{STACK_NAME}-vpce-gateway-{service}",
                vpc_id=vpc.id,
                service_name=service_name,
                route_table_ids=[private_route_table],
                vpc_endpoint_type=endpoint_type,
                tags={
                    "Name": f"{STACK_NAME}-vpce-gateway-{service}",
                },
            )

    # creating VPC endpoints
    vpce(
        "Interface",
        "ec2messages",
        f"com.amazonaws.{REGION}.ec2messages",
    )
    vpce(
        "Interface",
        "ssmmessages",
        f"com.amazonaws.{REGION}.ssmmessages",
    )
    vpce("Interface", "ssmendpoint", f"com.amazonaws.{REGION}.ssm")
    vpce("Gateway", "s3", f"com.amazonaws.{REGION}.s3")

    # creating Network ACLs
    nacl(public_subnets, "public")
    nacl(private_subnets, "private")

    # return pulumi.Output.from_input(vpc.id)
    return vpc


# creating networkhub vpc
create_vpc("networkhub-demo")
