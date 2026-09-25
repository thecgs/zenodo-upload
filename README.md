# zenodo-upload
upload big files to Zenodo.org using cURL, jq and bash

Uploading big files to https://zenodo.org from the commandline using the [Zenodo API](http://developers.zenodo.org/) is not straight forward. [zenodo_upload.sh](./zenodo_upload.sh) tries to make this a little easier. 

Inspired by Max Ogden's gist at https://gist.github.com/maxogden/b758cf0fe6d353846ef9ce7d03fdca0c .

# prerequisites

1. [jq](https://stedolan.github.io/jq/)
2. curl 
3. bash 

# usage

1. open a terminal
2. clone this repository
3. set environment variable ```ZENODO_TOKEN``` to Zenodo access token (see https://zenodo.org/account/settings/applications/tokens/new/) 
```bash
export ZENODO_TOKEN=[Zenodo access token]
```
4. create a new publication in Zenodo using the web ui, enter some information (e.g., title) and click ```save```
5. in the browser, copy the deposition id (e.g., in ```https://zenodo.org/deposit/12345``` , 12345 is the deposition id)
6. in terminal and upload a file using
```bash
./zenodo_upload.sh [deposition id] [filename] [--verbose/-v, optional]
```
7. on completion, you should see something like:
```shell
+ curl ...
######################################################################## 100.0%
```
8. in the web ui, refresh the deposition page and observe that the file was uploaded.



# Optional Python uploader

In addition to the original Bash/cURL workflow, an optional Python uploader is provided in `zenodo_upload.py`.

The Python implementation is intended for users who need additional reliability features when uploading large or multiple files to Zenodo, while keeping the existing shell scripts unchanged.

## Requirements

Python 3 and the `requests` package are required:

```bash
python3 -m pip install requests
```

## Set the Zenodo access token

Create a Zenodo personal access token and export it as the `ZENODO_TOKEN` environment variable:

```bash
export ZENODO_TOKEN="YOUR_ZENODO_ACCESS_TOKEN"
```

Using an environment variable is recommended instead of passing the token directly on the command line.

## Usage

### Upload a file to an existing Zenodo draft

If you already created a draft record on Zenodo, provide its deposition ID with `--deposition-id`:

```bash
python3 zenodo_upload.py \
    myfile.tar.gz \
    --deposition-id 12345678
```

For example, if the Zenodo draft URL contains:

```text
https://zenodo.org/uploads/12345678
```

then the deposition ID is:

```text
12345678
```

### Upload multiple files

Multiple files can be uploaded in a single command:

```bash
python3 zenodo_upload.py \
    file1.tar.gz \
    file2.tar.gz \
    file3.sif \
    --deposition-id 12345678
```

### Create a new draft

If `--deposition-id` is omitted, the script creates a new Zenodo draft automatically:

```bash
python3 zenodo_upload.py myfile.tar.gz
```

The new deposition ID will be printed in the terminal.

By default, the record remains unpublished.

### Upload with metadata

Metadata can also be supplied from the command line:

```bash
python3 zenodo_upload.py \
    myfile.tar.gz \
    --title "Example dataset" \
    --creator "Chen, Guisen::Hainan University" \
    --description "Example dataset uploaded using zenodo_upload.py" \
    --upload-type dataset
```

Multiple creators can be supplied by repeating `--creator`:

```bash
python3 zenodo_upload.py \
    myfile.tar.gz \
    --title "Example dataset" \
    --creator "Chen, Guisen::Hainan University" \
    --creator "Doe, Jane::Example University" \
    --description "Example research dataset"
```

### Retry failed uploads

Transient network or server errors are retried automatically.

The number of attempts can be changed with:

```bash
python3 zenodo_upload.py \
    large_file.sif \
    --deposition-id 12345678 \
    --retries 10
```

For very large files, the upload timeout can also be increased:

```bash
python3 zenodo_upload.py \
    large_file.sif \
    --deposition-id 12345678 \
    --upload-timeout 21600
```

### Zenodo Sandbox

The Zenodo Sandbox can be used for testing:

```bash
python3 zenodo_upload.py \
    test.tar.gz \
    --sandbox
```

### Publish after upload

To publish the record immediately after a successful upload, use:

```bash
python3 zenodo_upload.py \
    myfile.tar.gz \
    --title "Example dataset" \
    --creator "Chen, Guisen::Hainan University" \
    --description "Example dataset" \
    --publish
```

For important or large deposits, it is usually safer to omit `--publish`, inspect the draft in the Zenodo web interface, and publish it manually after confirming that all files and metadata are correct.

## File verification

Before uploading, the script calculates the MD5 checksum of each local file.

If a file with the same name, size, and checksum is already present in the Zenodo draft, the file is skipped automatically.

After a successful upload, the returned file size and checksum are also checked when available.

## Help

All command-line options can be displayed with:

```bash
python3 zenodo_upload.py --help
```
