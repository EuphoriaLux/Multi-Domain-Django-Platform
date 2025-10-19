"""
Azure Blob Storage reader for FinOps cost exports
Handles gzip-compressed CSV files from Azure Cost Management exports
"""
import os
import gzip
import io
from datetime import datetime
from azure.storage.blob import BlobServiceClient, ContainerClient
from django.conf import settings


class AzureCostBlobReader:
    """
    Read and process Azure Cost Management exports from Blob Storage

    Container: msexports

    Supported path patterns:
    1. Pattern A (PartnerLed): partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz
       Example: partnerled/PartnerLed-power_up/20251001-20251031/1f8d218f-.../part_0_0001.csv.gz

    2. Pattern B (Pay-as-you-go): {subscription}/{export_name}/{date_range}/{guid}/part_*.csv.gz
       Example: Pay as you go - Tom Privat/CostFocus-PayasyouGo/20251001-20251031/621cbd02-.../part_1_0001.csv.gz
    """

    def __init__(self):
        """Initialize Azure Blob Service Client using Django settings"""
        account_name = os.getenv('AZURE_ACCOUNT_NAME') or getattr(settings, 'AZURE_ACCOUNT_NAME', None)
        account_key = os.getenv('AZURE_ACCOUNT_KEY') or getattr(settings, 'AZURE_ACCOUNT_KEY', None)

        if not account_name or not account_key:
            raise ValueError("Azure Storage credentials not configured. Set AZURE_ACCOUNT_NAME and AZURE_ACCOUNT_KEY environment variables.")

        self.account_name = account_name
        self.account_url = f"https://{account_name}.blob.core.windows.net"
        self.blob_service_client = BlobServiceClient(
            account_url=self.account_url,
            credential=account_key
        )
        self.container_name = 'msexports'
        self.container_client = self.blob_service_client.get_container_client(self.container_name)

    def list_cost_exports(self, prefix='', subscription_filter=None):
        """
        List all cost export CSV files in the msexports container

        Args:
            prefix: Blob path prefix (default: '' to scan entire container)
            subscription_filter: Optional subscription name to filter (e.g., 'PartnerLed-power_up')

        Returns:
            List of dicts with blob metadata:
            {
                'blob_path': str,
                'blob_name': str,
                'size': int,
                'last_modified': datetime,
                'subscription_name': str,
                'export_name': str,  # Export configuration name (if applicable)
                'date_range': str,
                'guid': str,
                'path_pattern': str  # 'partnerled' or 'pay-as-you-go'
            }
        """
        exports = []

        # List all blobs with the prefix
        blob_list = self.container_client.list_blobs(name_starts_with=prefix)

        for blob in blob_list:
            # Only process .csv.gz files
            if not blob.name.endswith('.csv.gz'):
                continue

            # Parse blob path dynamically to handle multiple patterns
            parsed_data = self._parse_blob_path(blob.name)

            if not parsed_data:
                continue  # Invalid or unsupported path structure

            # Apply subscription filter if specified
            if subscription_filter and parsed_data['subscription_name'] != subscription_filter:
                continue

            # Add blob metadata
            parsed_data.update({
                'blob_path': blob.name,
                'blob_name': blob.name.split('/')[-1],
                'size': blob.size,
                'last_modified': blob.last_modified,
            })

            exports.append(parsed_data)

        # Sort by date range descending (most recent first)
        exports.sort(key=lambda x: x['date_range'], reverse=True)

        return exports

    def _parse_blob_path(self, blob_path):
        """
        Parse blob path and detect pattern type

        Handles two patterns:
        - Pattern A (PartnerLed): partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz
        - Pattern B (Pay-as-you-go): {subscription}/{export_name}/{date_range}/{guid}/part_*.csv.gz

        Args:
            blob_path: Full blob path string

        Returns:
            dict or None: Parsed metadata or None if invalid
        """
        path_parts = blob_path.split('/')

        # Pattern A: partnerled/{subscription}/{date_range}/{guid}/part_*.csv.gz (5 parts)
        if len(path_parts) == 5 and path_parts[0].lower() == 'partnerled':
            return {
                'subscription_name': path_parts[1],
                'export_name': 'partnerled',
                'date_range': path_parts[2],
                'guid': path_parts[3],
                'path_pattern': 'partnerled',
            }

        # Pattern B: {subscription}/{export_name}/{date_range}/{guid}/part_*.csv.gz (5 parts)
        elif len(path_parts) == 5 and path_parts[0].lower() != 'partnerled':
            return {
                'subscription_name': path_parts[0],
                'export_name': path_parts[1],
                'date_range': path_parts[2],
                'guid': path_parts[3],
                'path_pattern': 'pay-as-you-go',
            }

        # Unknown pattern
        return None

    def parse_date_range(self, date_range_str):
        """
        Parse date range string (YYYYMMDD-YYYYMMDD) into start and end dates

        Args:
            date_range_str: String like '20251001-20251031'

        Returns:
            tuple: (start_date, end_date) as datetime.date objects
        """
        try:
            start_str, end_str = date_range_str.split('-')
            start_date = datetime.strptime(start_str, '%Y%m%d').date()
            end_date = datetime.strptime(end_str, '%Y%m%d').date()
            return start_date, end_date
        except (ValueError, AttributeError):
            return None, None

    def download_and_decompress(self, blob_path):
        """
        Download and decompress a gzipped CSV file from blob storage

        Args:
            blob_path: Full blob path
                Examples:
                - 'partnerled/PartnerLed-power_up/20251001-20251031/.../part_0_0001.csv.gz'
                - 'Pay as you go - Tom Privat/CostFocus-PayasyouGo/20251001-20251031/.../part_1_0001.csv.gz'

        Returns:
            io.StringIO: Decompressed CSV content as a text stream
        """
        # Get blob client
        blob_client = self.blob_service_client.get_blob_client(
            container=self.container_name,
            blob=blob_path
        )

        # Download blob content as bytes (Azure SDK 12.x compatible)
        stream_downloader = blob_client.download_blob()

        # Read all bytes from the stream
        compressed_data = b""
        for chunk in stream_downloader.chunks():
            compressed_data += chunk

        # Decompress gzip data
        with gzip.GzipFile(fileobj=io.BytesIO(compressed_data)) as gz_file:
            decompressed_data = gz_file.read()

        # Convert bytes to string and create StringIO object
        text_data = decompressed_data.decode('utf-8-sig')  # utf-8-sig handles BOM
        return io.StringIO(text_data)

    def stream_csv_records(self, blob_path, batch_size=1000):
        """
        Stream CSV records from a gzipped blob in batches

        Args:
            blob_path: Full blob path
            batch_size: Number of records to yield at once

        Yields:
            list: Batches of CSV rows as dictionaries
        """
        import csv

        csv_stream = self.download_and_decompress(blob_path)
        csv_reader = csv.DictReader(csv_stream)

        batch = []
        for row in csv_reader:
            batch.append(row)

            if len(batch) >= batch_size:
                yield batch
                batch = []

        # Yield remaining records
        if batch:
            yield batch

    def get_blob_info(self, blob_path):
        """
        Get metadata about a specific blob

        Args:
            blob_path: Full blob path

        Returns:
            dict: Blob properties (size, last_modified, etc.)
        """
        blob_client = self.blob_service_client.get_blob_client(
            container=self.container_name,
            blob=blob_path
        )

        properties = blob_client.get_blob_properties()

        return {
            'name': properties.name,
            'size': properties.size,
            'last_modified': properties.last_modified,
            'content_type': properties.content_settings.content_type,
        }
