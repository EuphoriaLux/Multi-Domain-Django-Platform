from django.core.management.base import BaseCommand
from django.core.files import File
from vinsdelux.models import VdlAdoptionPlan, VdlAdoptionPlanImage
import os
from django.conf import settings

class Command(BaseCommand):
    help = 'Assigns the existing adoption plan images to their respective adoption plans'

    def handle(self, *args, **options):
        # Media adoption plan images path
        media_path = os.path.join(settings.MEDIA_ROOT, 'adoption_plans', '2025', '08')
        
        # Map of adoption plan IDs to their image filenames
        image_mapping = {
            1: 'adoptionplan (1).png',
            2: 'adoptionplan (2).png',
            3: 'adoptionplan (3).png',
            4: 'adoptionplan (4).png',
            5: 'adoptionplan (5).png',
        }
        
        for plan_id, filename in image_mapping.items():
            try:
                plan = VdlAdoptionPlan.objects.get(id=plan_id)
                file_path = os.path.join(media_path, filename)
                
                if os.path.exists(file_path):
                    # Check if image already exists for this plan
                    existing_images = plan.images.filter(is_primary=True)
                    if existing_images.exists():
                        self.stdout.write(f'Plan {plan_id} already has primary image, skipping...')
                        continue
                    
                    # Create the VdlAdoptionPlanImage instance
                    with open(file_path, 'rb') as f:
                        image = VdlAdoptionPlanImage.objects.create(
                            adoption_plan=plan,
                            image=File(f, name=filename),
                            order=0,
                            is_primary=True,
                            caption=f'{plan.name} - Primary Image'
                        )
                    
                    self.stdout.write(self.style.SUCCESS(
                        f'Successfully assigned {filename} to {plan.name}'
                    ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f'Image file not found: {file_path}'
                    ))
                    
            except VdlAdoptionPlan.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Adoption plan with ID {plan_id} not found'
                ))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f'Error processing plan {plan_id}: {str(e)}'
                ))
        
        # Display summary
        self.stdout.write('\n' + '='*50)
        for plan in VdlAdoptionPlan.objects.all():
            image_count = plan.images.count()
            self.stdout.write(
                f'Plan {plan.id} ({plan.name}): {image_count} images'
            )