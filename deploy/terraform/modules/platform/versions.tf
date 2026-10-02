terraform {
  required_version = ">= 1.6"
  required_providers {
    google      = { source = "hashicorp/google", version = ">= 6.50, < 8.0" }
    google-beta = { source = "hashicorp/google-beta", version = ">= 6.50, < 8.0" }
    random      = { source = "hashicorp/random", version = ">= 3.6, < 4.0" }
  }
}
