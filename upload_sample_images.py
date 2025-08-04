#!/usr/bin/env python
"""
Script to upload sample images to Azure Blob Storage during deployment.
This can be run as part of the deployment process or manually.
"""

import os
import sys
import django
from pathlib import Path

# Setup Django
sys.path.append('/tmp/8ddd393221816ec')  # Adjust path as needed
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.production')
django.setup()

from django.conf import settings
from azure.storage.blob import BlobServiceClient
import requests
from io import BytesIO

def get_blob_service_client():
    """Get Azure Blob Storage client"""
    account_name = getattr(settings, 'AZURE_ACCOUNT_NAME', None)
    account_key = getattr(settings, 'AZURE_ACCOUNT_KEY', None)
    container_name = getattr(settings, 'AZURE_CONTAINER_NAME', None)
    
    if not all([account_name, account_key, container_name]):
        raise ValueError("Azure storage credentials not properly configured")
    
    connection_string = f"DefaultEndpointsProtocol=https;AccountName={account_name};AccountKey={account_key};EndpointSuffix=core.windows.net"
    return BlobServiceClient.from_connection_string(connection_string), container_name

def download_and_upload_image(url, blob_name, description=""):
    """Download image from URL and upload to Azure Blob Storage"""
    try:
        blob_service_client, container_name = get_blob_service_client()
        
        # Download image
        print(f"Downloading {description}: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Upload to blob storage
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.upload_blob(BytesIO(response.content), overwrite=True)
        
        blob_url = f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/{container_name}/{blob_name}"
        print(f"✅ Uploaded: {blob_url}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to upload {blob_name}: {e}")
        return False

def upload_sample_images():
    """Upload sample wine and producer images"""
    print("🍷 Starting sample image upload to Azure Blob Storage...")
    
    # Sample wine bottle images (using placeholder/stock images)
    wine_images = [
        {
            'url': 'https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle1.png', 
            'desc': 'Red wine bottle 1'
        },
        {
            'url': 'https://images.unsplash.com/photo-1506377247377-2a5b3b417ebb?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle2.png', 
            'desc': 'White wine bottle 1'
        },
        {
            'url': 'https://images.unsplash.com/photo-1571613316887-6f8d5cbf7ef7?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle3.png', 
            'desc': 'Wine bottles arrangement'
        },
        {
            'url': 'https://images.unsplash.com/photo-1574870111867-089730e5a72e?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle4.png', 
            'desc': 'Elegant wine bottle'
        },
        {
            'url': 'https://images.unsplash.com/photo-1586370434639-0fe43b2d32d6?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle5.png', 
            'desc': 'Premium wine bottle'
        },
        {
            'url': 'https://images.unsplash.com/photo-1515824065884-5a2d0e6fd06d?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle6.png', 
            'desc': 'Wine collection'
        },
        {
            'url': 'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle7.png', 
            'desc': 'Vintage wine bottle'
        },
        {
            'url': 'https://images.unsplash.com/photo-1547595628-c61a29f496f0?w=800&h=600&fit=crop&crop=center',
            'blob': 'products/gallery/winebottle8.png', 
            'desc': 'Champagne bottle'
        }
    ]
    
    # Producer logos/images
    producer_images = [
        {
            'url': 'https://images.unsplash.com/photo-1571613316887-6f8d5cbf7ef7?w=400&h=400&fit=crop&crop=center',
            'blob': 'producers/logos/producer1.png', 
            'desc': 'Château Margaux logo'
        },
        {
            'url': 'https://images.unsplash.com/photo-1586370434639-0fe43b2d32d6?w=400&h=400&fit=crop&crop=center',
            'blob': 'producers/logos/producer2.png', 
            'desc': 'Domaine de la Romanée-Conti logo'
        },
        {
            'url': 'https://images.unsplash.com/photo-1553361371-9b22f78e8b1d?w=400&h=400&fit=crop&crop=center',
            'blob': 'producers/logos/producer3.png', 
            'desc': 'Penfolds logo'
        },
        {
            'url': 'https://images.unsplash.com/photo-1506377247377-2a5b3b417ebb?w=400&h=400&fit=crop&crop=center',
            'blob': 'producers/logos/producer4.png', 
            'desc': 'Antinori logo'
        },
        {
            'url': 'https://images.unsplash.com/photo-1574870111867-089730e5a72e?w=400&h=400&fit=crop&crop=center',
            'blob': 'producers/logos/producer5.png', 
            'desc': 'Catena Zapata logo'
        }
    ]
    
    # Producer photos
    producer_photos = [
        {
            'url': 'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=600&h=400&fit=crop&crop=center',
            'blob': 'homepage/producer1.png', 
            'desc': 'Vineyard landscape'
        }
    ]
    
    # Homepage images
    homepage_images = [
        {
            'url': 'https://images.unsplash.com/photo-1564760055775-d63b17a55c44?w=1200&h=600&fit=crop&crop=center',
            'blob': 'homepage/hero-section.png', 
            'desc': 'Hero section background'
        }
    ]
    
    # Upload all images
    all_images = wine_images + producer_images + producer_photos + homepage_images
    
    success_count = 0
    total_count = len(all_images)
    
    for image in all_images:
        if download_and_upload_image(image['url'], image['blob'], image['desc']):
            success_count += 1
    
    print(f"\n🎉 Upload complete: {success_count}/{total_count} images uploaded successfully!")
    
    if success_count == total_count:
        print("✅ All images uploaded! You can now run: python manage.py populate_data")
    else:
        print("⚠️  Some images failed to upload. Check the errors above.")

if __name__ == "__main__":
    upload_sample_images()