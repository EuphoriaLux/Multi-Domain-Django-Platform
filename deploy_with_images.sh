#!/bin/bash
# 🚀 Complete deployment script with automated image handling
# This script should be run after successful Azure deployment

echo "🍷 Starting Vins de Lux deployment with automated images..."

# Install required Python packages if not already installed
echo "📦 Installing required packages..."
pip install requests Pillow

# Clear any existing data (optional - remove this line for production)
echo "🗑️  Clearing existing sample data..."
python manage.py shell -c "
from vinsdelux.models import *
VdlProductImage.objects.all().delete()
VdlAdoptionPlan.objects.all().delete() 
VdlCoffret.objects.all().delete()
VdlProducer.objects.all().delete()
print('✅ Sample data cleared')
"

# Run the enhanced populate command with images
echo "📊 Populating database with sample data and images..."
python manage.py populate_with_images

# Verify the deployment
echo "🔍 Verifying deployment..."
python manage.py shell -c "
from vinsdelux.models import *
producers = VdlProducer.objects.count()
coffrets = VdlCoffret.objects.count()
plans = VdlAdoptionPlan.objects.count()
images = VdlProductImage.objects.count()
print(f'✅ Created: {producers} producers, {coffrets} coffrets, {plans} plans, {images} images')
"

# Test Azure Blob Storage
echo "☁️  Testing Azure Blob Storage..."
python manage.py test_azure_upload

echo ""
echo "🎉 Deployment complete!"
echo ""
echo "🌐 Test your website:"
echo "   Homepage: https://vinsdelux.com/"
echo "   Products: https://vinsdelux.com/coffrets/"
echo "   Producers: https://vinsdelux.com/producers/"
echo "   Admin: https://vinsdelux.com/admin/"
echo ""
echo "📸 All product images have been automatically downloaded and uploaded!"