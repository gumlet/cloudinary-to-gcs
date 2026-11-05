import mimetypes
import os
import re
from tqdm import tqdm
import requests
import argparse
import cloudinary
import cloudinary.uploader
import cloudinary.api
from google.cloud import storage
import concurrent.futures

# Extensions missing from the stdlib map on older Python versions.
# Used only when the origin response has no specific Content-Type.
_EXTRA_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".jfif": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}

_GENERIC_CONTENT_TYPES = (
    "",
    "application/octet-stream",
    "binary/octet-stream",
    "application/x-download",
)


def content_type_for_upload(response, *paths):
    """MIME type to store on the GCS object.

    upload_from_string defaults to text/plain, which breaks image, video, and
    raw previews. Prefer the origin Content-Type, then the file extension.
    """
    header = (response.headers.get("Content-Type") or "").strip()
    media_type = header.split(";", 1)[0].strip()
    if media_type.lower() not in _GENERIC_CONTENT_TYPES:
        return header

    for path in paths:
        guessed = _guess_content_type(path)
        if guessed:
            return guessed

    return media_type or "application/octet-stream"


def _guess_content_type(path):
    if not path:
        return None
    clean = path.split("?", 1)[0].split("#", 1)[0]
    guessed, _encoding = mimetypes.guess_type(clean)
    if guessed:
        return guessed
    extension = os.path.splitext(clean)[1].lower()
    return _EXTRA_CONTENT_TYPES.get(extension)

def parse_bool(value):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes", "y", "t"):
        return True
    if normalized in ("false", "0", "no", "n", "f"):
        return False
    raise argparse.ArgumentTypeError(
        "expected a boolean (true/false), got {!r}".format(value)
    )

def set_cloudinary_config(cloud_name, api_key, api_secret):
    try:
        cloudinary.config(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
        return False, ""
    except Exception as err:
        return True, str(err)

def create_gcs_client(gcs_credentials_path, gcs_project_id):
    try:
        if gcs_credentials_path:
            if gcs_project_id:
                gcs = storage.Client.from_service_account_json(
                    gcs_credentials_path,
                    project=gcs_project_id,
                )
            else:
                gcs = storage.Client.from_service_account_json(gcs_credentials_path)
        elif gcs_project_id:
            gcs = storage.Client(project=gcs_project_id)
        else:
            gcs = storage.Client()
        return False, gcs
    except Exception as err:
        return True, str(err)

def migrate_cloudinary_resources(resource_types, gcs):

    try:
        resource_types = resource_types.split(",")
        source_buckets = args.source_buckets.split(",")
        batch_index = 0
        batch_size = 500
        resource_url_list_dict = {}

        with requests.Session() as sess:
            with concurrent.futures.ThreadPoolExecutor(max_workers = args.max_worker) as executor:

                for rs_type in resource_types:
                    rs_type = rs_type.strip()

                    resources = cloudinary.api.resources(resource_type=rs_type,max_results=batch_size)
                    while resources and resources["resources"]:
                        batch_index += 1
                        current_batch = list(map(lambda x: x["url"], resources["resources"]))
                        if args.source_buckets:
                            current_batch = filter_urls_base_on_folder_names(source_buckets,current_batch)

                        source_to_target_mapper_list = source_to_target_mapper(
                            current_batch,
                            args.keep_cloud_name_in_path,
                            args.target_parent_path
                        )

                        if batch_index == 1:
                            show_sample_mapping(
                                source_to_target_mapper_list,
                                args.gcs_bucket_name)

                            confirm = input("Please check your input data once and confirm : [yes]")
                            if confirm.lower() != 'yes':
                                print("Migration cancelled.")
                                return
                            print("\n")

                        print("Migrating batch {} ...".format(batch_index))
                        #start migrating....
                        pbar = tqdm(total=len(source_to_target_mapper_list))
                        progress = 0
                        failed_count = 0
                        results = []

                        future_to_data = {executor.submit(migrate_data, data, gcs, args.gcs_bucket_name, sess): data for data in source_to_target_mapper_list}
                        for future in concurrent.futures.as_completed(future_to_data):
                            progress += 1
                            pbar.update(1)
                            try:
                                err,resp = future.result()
                            except Exception as ex:
                                print("Exception : ",ex)
                                failed_count += 1
                                results.append(future_to_data[future])
                                continue

                            if err:
                                failed_count += 1
                                results.append(resp)

                        pbar.close()

                        if failed_count > 0:
                            print("Following resources failed:")
                            for r in results:
                                print("source URL: {}".format(r))

                        # get the next batch....
                        if "next_cursor" in resources:
                            resources = cloudinary.api.resources(resource_type=rs_type, max_results=batch_size, next_cursor=resources["next_cursor"])
                        else:
                            break

    except Exception as err:
        print("Error ocurred in getting cloudinary resource URLs: {}".format(str(err)))
        return {}

def filter_urls_base_on_folder_names(source_buckets, url_list):
    if isinstance(source_buckets, str):
        source_buckets = source_buckets.split(",")
    filtered_urls = []
    for sb in source_buckets:
        sb = sb.strip()
        if not sb:
            continue
        for url in url_list:
            if sb in url:
                filtered_urls.append(url)
    return list(set(filtered_urls))

def source_to_target_mapper(url_list, keep_same_structure, parent_path):
    source_to_target_list = []
    for url in url_list:
        if not keep_same_structure:
            url_split = re.split(r"(\bimage\b|\bvideo\b|\braw\b)/upload/v[0-9]+/", url)[-1]
        else:
            url_split = "/".join(url.split("/")[3:])
        if parent_path:
            url_split = parent_path.strip("/") + "/" + url_split
        source_to_target_list.append([url, url_split])
    return source_to_target_list

def show_sample_mapping(source_to_target_mapper_dict, gcs_bucket_name):
    print("\n#Sample Mapping")
    print("------------------------------------------------------")
    for source,target in source_to_target_mapper_dict[:5]:
        target = "gs://" + gcs_bucket_name + "/" + target
        print("{} ==> {}".format(source,target))

    print("\n")

def migrate_data(data, gcs_client, gcs_bucket_name, session):
    source, target = data
    bucket = gcs_client.bucket(gcs_bucket_name)
    blob = bucket.blob(target)

    if args.resuming_migration:
        #ignore if already uploaded to gcs...
        try:
            if blob.exists():
                return False, data
        except Exception as head_err:
            pass

    try:
        res = session.get(source)
    except Exception as get_err:
        return True, data

    try:
        content_type = content_type_for_upload(res, source, target)
        blob.upload_from_string(res.content, content_type=content_type)
    except Exception as put_err:
        return True, data
    return False, data

def run(args):

    err, msg = set_cloudinary_config(
        args.cloudinary_cloud_name,
        args.cloudinary_api_key,
        args.cloudinary_api_secret,
    )

    if err:
        print("Error {} ocurred in connecting cloudinary. check config.".format(msg))
        return

    err, gcs = create_gcs_client(
        args.gcs_credentials_path,
        args.gcs_project_id,
    )

    if err:
        print("Error {} ocurred in creating gcs client. check config.".format(gcs))
        return

    migrate_cloudinary_resources(args.resource_types,gcs)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        "Script to migrate cloudinary resources to Google Cloud Storage."
    )

    parser.add_argument(
        "--cloudinary_cloud_name",
        type=str,
        required=True,
        help="souce cloudinary cloud name.",
    )
    parser.add_argument(
        "--cloudinary_api_key",
        type=str,
        required=True,
        help="source cloudinary API key.",
    )
    parser.add_argument(
        "--cloudinary_api_secret",
        type=str,
        required=True,
        help="source cloudinary API secret.",
    )
    parser.add_argument(
        "--gcs_credentials_path",
        type=str,
        required=False,
        default="",
        help="Path to GCS service account JSON credentials file. "
             "If omitted, Application Default Credentials are used.",
    )
    parser.add_argument(
        "--gcs_project_id",
        type=str,
        required=False,
        default="",
        help="GCS project ID. Optional when provided by the credentials file.",
    )
    parser.add_argument(
        "--gcs_bucket_name",
        type=str,
        required=True,
        help="GCS target bucket name."
    )
    parser.add_argument(
        "--resource_types",
        type=str,
        default="image,raw,video",
        help="comma seperated list of resource types (image, raw, video) to be migrated.",
    )
    parser.add_argument(
        "--keep_cloud_name_in_path",
        type=parse_bool,
        default=False,
        help="When kept this argument True it script will create target structure with 'cloud_name/:resource_type/upload' \
            otherwise (default case) target structure will be same as you can see in your cloudinary Media Library.",
    )
    parser.add_argument(
        "--source_buckets",
        type=str,
        default="",
        help="Given comma seperated list of buckets/folder names, script will only migrate resource following \
            bucket names/folder names. Provide only absolute paths.(format: '/samples/rats,/samples/birds')",
    )
    parser.add_argument(
        "--target_parent_path",
        type=str,
        default="",
        help="Target structure will be created following specified path. (prefix path)",
    )

    parser.add_argument(
        "--resuming_migration",
        type=parse_bool,
        default=False,
        help="When kept this argument true it will skip the object already migrated to GCS.",
    )

    parser.add_argument(
        "--max_worker",
        type=int,
        default=25,
        help="It limits maximum concurrent threads created during migration of resources.",
    )

    args = parser.parse_args()


    run(args)
