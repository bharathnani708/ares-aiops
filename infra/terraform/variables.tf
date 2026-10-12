variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource; also the EKS cluster name"
  type        = string
  default     = "ares-aiops"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "az_count" {
  type    = number
  default = 2
}

variable "kubernetes_version" {
  description = "Pick a version in standard support: aws eks describe-cluster-versions --query 'clusterVersions[?status==`STANDARD_SUPPORT`].clusterVersion'"
  type        = string
  default     = "1.35"
}

variable "admin_principal_arn" {
  description = "IAM user/role ARN that gets cluster-admin via an EKS access entry (you, on your laptop)"
  type        = string
}

variable "cluster_endpoint_public_cidrs" {
  description = "CIDRs allowed to reach the public Kubernetes API endpoint. Use your own /32."
  type        = list(string)

  validation {
    condition     = length(var.cluster_endpoint_public_cidrs) > 0 && !contains(var.cluster_endpoint_public_cidrs, "0.0.0.0/0")
    error_message = "Restrict the API endpoint to specific CIDRs (for example your IP/32), not 0.0.0.0/0."
  }
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "node_capacity_type" {
  description = "ON_DEMAND or SPOT (SPOT is ~60-70% cheaper and fine for a demo)"
  type        = string
  default     = "ON_DEMAND"
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "bedrock_model_id" {
  description = "Claude model on Amazon Bedrock used by the analyst"
  type        = string
  default     = "anthropic.claude-opus-5-5"
}

variable "analyst_effort" {
  description = "Claude effort level: low (fastest/cheapest), medium, high"
  type        = string
  default     = "medium"
}

variable "lambda_reserved_concurrency" {
  description = "Cap on concurrent analyst runs (cost guard). -1 = unreserved; new accounts may need -1."
  type        = number
  default     = -1
}
