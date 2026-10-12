output "region" { value = var.region }
output "cluster_name" { value = aws_eks_cluster.this.name }
output "ecr_repositories" { value = { for k, r in aws_ecr_repository.app : k => r.repository_url } }
output "ecr_registry" { value = split("/", aws_ecr_repository.app["habitat-api"].repository_url)[0] }
output "analyst_function_name" { value = aws_lambda_function.analyst.function_name }
output "workloads_log_group" { value = aws_cloudwatch_log_group.workloads.name }
output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}
