# Cloudinary-to-GCS

Utility script to copy files from Cloudinary to a Google Cloud Storage bucket.

## Requirements

- python >= 3.6
- python-pip3

## Installation

To install necessary packages for script, run following command.

`pip3 install -r requirements.txt`

## Usage

This script requires following arguments (necessary to run):

- Cloudinary Cloud Name
- Cloudinary API key
- Cloudinary API Secret
- GCS Bucket name

authentication arguments:

- GCS credentials path (service account JSON). Please make sure you are giving "Storage Admin Access for the bucket"
- GCS project ID (when included in the credentials file)

For example,

```bash
python3 storage_migration.py \
    --cloudinary_cloud_name cloudinary_cloud_name \
    --cloudinary_api_key cloudinary_api_key \
    --cloudinary_api_secret cloudinary_api_secret \
    --gcs_credentials_path /path/to/service-account.json \
    --gcs_project_id gcs_project_id \
    --gcs_bucket_name gcs_bucket_name
```

If you are re-running this script then use following argument to speedup the performance.

`--resuming_migration True`

To update te max no of worker (which handles migration process in paraller) use the following argument (default is 25).

`--max_worker 50`


For more info, run following commmand.

```bash
python3 storage_migration.py --help
```
