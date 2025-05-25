from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from vinsdelux.models import VdlUserProfile, VdlAddress, VdlCategory, VdlProducer, VdlProduct, VdlProductImage, VdlAdoptionTier, VdlOrder, VdlOrderItem, VdlBlogPostCategory, VdlBlogPost
from django.utils.text import slugify
import random

class Command(BaseCommand):
    help = 'Populates the database with test data'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting data population...'))

        # Create users
        users = []
        for i in range(5):
            username = f'user{i}'
            if User.objects.filter(username=username).exists():
                user = User.objects.get(username=username)
            else:
                user = User.objects.create_user(username=username, password='password')
            users.append(user)
            if not VdlUserProfile.objects.filter(user=user).exists():
                VdlUserProfile.objects.create(user=user, phone_number='123-456-7890')

        self.stdout.write(self.style.SUCCESS('Starting data population...'))

        # Create users
        users = []
        for i in range(5):
            username = f'user{i}'
            if User.objects.filter(username=username).exists():
                user = User.objects.get(username=username)
            else:
                user = User.objects.create_user(username=username, password='password')
            users.append(user)
            if not VdlUserProfile.objects.filter(user=user).exists():
                VdlUserProfile.objects.create(user=user, phone_number='123-456-7890')

        # Create categories (using provided data)
        self.stdout.write(self.style.SUCCESS('Creating categories...'))
        # Parent Category
        cat_vins_lux, created = VdlCategory.objects.get_or_create(
            slug="vins-luxembourgeois",
            defaults={
                "name": "Vins Luxembourgeois",
                "description": "Tous les vins produits au Grand-Duché de Luxembourg.",
                "image": "categories/vins_lux_banner.jpg",
                "is_active": True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created category: {cat_vins_lux.name}'))
        else:
             self.stdout.write(self.style.WARNING(f'Category already exists: {cat_vins_lux.name}'))


        # Sub-categories
        categories_data = [
            {
                "name": "Crémant de Luxembourg",
                "slug": "cremant-de-luxembourg",
                "description": "Vins mousseux de qualité, méthode traditionnelle.",
                "image": "categories/cremant.jpg",
                "parent": cat_vins_lux,
                "is_active": True
            },
            {
                "name": "Vin Blanc Sec",
                "slug": "vin-blanc-sec-lux",
                "description": "Vins blancs secs typiques de la Moselle luxembourgeoise.",
                "image": "categories/vin_blanc.jpg",
                "parent": cat_vins_lux,
                "is_active": True
            },
            {
                "name": "Vin Rosé",
                "slug": "vin-rose-lux",
                "description": "Vins rosés frais et fruités du Luxembourg.",
                "image": "categories/vin_rose.jpg",
                "parent": cat_vins_lux,
                "is_active": True
            },
            {
                "name": "Vin Rouge",
                "slug": "vin-rouge-lux",
                "description": "Vins rouges luxembourgeois, souvent à base de Pinot Noir.",
                "image": "categories/vin_rouge.jpg",
                "parent": cat_vins_lux,
                "is_active": True
            },
            {
                "name": "Cuvées Spéciales",
                "slug": "cuvees-speciales-lux",
                "description": "Sélections spéciales et éditions limitées.",
                "image": "categories/cuvees_speciales.jpg",
                "parent": cat_vins_lux,
                "is_active": True
            }
        ]

        for cat_data in categories_data:
             category, created = VdlCategory.objects.get_or_create(
                slug=cat_data['slug'],
                defaults=cat_data
            )
             if created:
                self.stdout.write(self.style.SUCCESS(f'Created category: {category.name}'))
             else:
                self.stdout.write(self.style.WARNING(f'Category already exists: {category.name}'))


        # Create producers (using provided data)
        self.stdout.write(self.style.SUCCESS('Creating producers...'))
        producers_data = [
            {
                "name": "Domaines Vinsmoselle",
                "slug": "domaines-vinsmoselle",
                "description": "Le plus grand producteur de vins et crémants au Luxembourg, fondé en 1921.",
                "logo": "producers/logos/vinsmoselle_logo.png",
                "producer_photo": "producers/photos/vinsmoselle_vineyard.jpg",
                "website": "https://www.vinsmoselle.lu",
                "region": "Moselle Luxembourgeoise",
                "is_featured_on_homepage": True
            },
            {
                "name": "Caves St Martin",
                "slug": "caves-st-martin",
                "description": "Producteur renommé pour ses crémants et vins fins, situé à Remich.",
                "logo": "producers/logos/st_martin_logo.png",
                "website": "https://www.cavesstmartin.lu",
                "region": "Moselle Luxembourgeoise",
                "is_featured_on_homepage": True
            },
            {
                "name": "Cep d'Or",
                "slug": "cep-d-or",
                "description": "Vigneron indépendant passionné, offrant des vins de caractère.",
                "logo": "producers/logos/cep_dor_logo.png",
                "producer_photo": "producers/photos/cep_dor_cellar.jpg",
                "website": "https://www.cepdor.lu",
                "region": "Moselle Luxembourgeoise"
            },
            {
                "name": "Domaine Clos des Rochers",
                "slug": "domaine-clos-des-rochers",
                "description": "Domaine familial produisant des vins élégants et des crémants.",
                "logo": "producers/logos/clos_rochers_logo.png",
                "website": "https://www.closdesrochers.lu",
                "region": "Grevenmacher"
            },
            {
                "name": "Schlink Domaine Viticole",
                "slug": "schlink-domaine-viticole",
                "description": "Tradition et modernité se rencontrent dans ce domaine viticole de la Moselle.",
                "logo": "producers/logos/schlink_logo.png",
                "website": "https://www.schlink.lu",
                "region": "Moselle Luxembourgeoise",
                "is_featured_on_homepage": False
            }
        ]

        producers_map = {} # To easily get producer by name later
        for producer_data in producers_data:
            producer, created = VdlProducer.objects.get_or_create(
                slug=producer_data['slug'],
                defaults=producer_data
            )
            producers_map[producer.name] = producer
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created producer: {producer.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'Producer already exists: {producer.name}'))


        # Create products (using provided data)
        self.stdout.write(self.style.SUCCESS('Creating products...'))
        # Assuming Category IDs: Crémant=2, Vin Blanc Sec=3, Vin Rosé=4, Vin Rouge=5, Cuvées Spéciales=6
        # Assuming Producer Names: Domaines Vinsmoselle, Caves St Martin, Cep d'Or, Domaine Clos des Rochers, Schlink Domaine Viticole

        # Fetch categories by slug for easier linking
        categories_map = {cat.slug: cat for cat in VdlCategory.objects.all()}


        products_data = [
            {
                "name": "Crémant Poll-Fabaire Cuvée Brut",
                "slug": "cremant-poll-fabaire-cuvee-brut",
                "category_slug": "cremant-de-luxembourg", # Link by slug
                "producer_name": "Domaines Vinsmoselle", # Link by name
                "short_description": "Un crémant classique, vif et élégant.",
                "full_description": "Ce Crémant de Luxembourg Brut de Poll-Fabaire (Domaines Vinsmoselle) est un assemblage harmonieux, parfait pour l'apéritif ou les célébrations.",
                "price": 12.50,
                "sku": "PF-CREM-BRUT-75",
                "stock_quantity": 150,
                "is_available": True,
                "is_featured": True,
                "main_image": "products/main/poll_fabaire_brut.jpg"
            },
            {
                "name": "Riesling Schengen Markusberg Grand Premier Cru",
                "slug": "riesling-schengen-markusberg-gpc",
                "category_slug": "vin-blanc-sec-lux", # Link by slug
                "producer_name": "Domaines Vinsmoselle", # Link by name
                "short_description": "Riesling fin et minéral du terroir de Schengen.",
                "full_description": "Un Grand Premier Cru de la Moselle Luxembourgeoise, ce Riesling du lieu-dit Markusberg à Schengen exprime pureté et complexité.",
                "price": 18.75,
                "sku": "VMO-RIE-SCHMKGPC-75",
                "stock_quantity": 80,
                "is_available": True,
                "is_featured": False, # Assuming not featured based on previous homepage display
                "main_image": "products/main/riesling_markusberg.jpg"
            },
            {
                "name": "Auxerrois Vieilles Vignes - Cep d'Or",
                "slug": "auxerrois-vieilles-vignes-cep-d-or",
                "category_slug": "vin-blanc-sec-lux", # Link by slug
                "producer_name": "Cep d'Or", # Link by name
                "short_description": "Auxerrois riche et aromatique issu de vieilles vignes.",
                "full_description": "Le Cep d'Or nous offre cet Auxerrois ample et fruité, idéal avec les plats luxembourgeois traditionnels.",
                "price": 14.90,
                "sku": "CDO-AUX-VV-75",
                "stock_quantity": 65,
                "is_available": True,
                "is_featured": True,
                "main_image": "products/main/cep_dor_auxerrois.jpg"
            },
            {
                "name": "Pinot Noir Rosé - Caves St Martin",
                "slug": "pinot-noir-rose-caves-st-martin",
                "category_slug": "vin-rose-lux", # Link by slug
                "producer_name": "Caves St Martin", # Link by name
                "short_description": "Un rosé délicat et fruité, parfait pour l'été.",
                "full_description": "Ce Pinot Noir Rosé des Caves St Martin séduit par sa fraîcheur et ses arômes de fruits rouges. Idéal en terrasse.",
                "price": 11.20,
                "sku": "CSM-PNR-75",
                "stock_quantity": 120,
                "is_available": True,
                "is_featured": False, # Assuming not featured
                "main_image": "products/main/st_martin_rose.jpg"
            },
            {
                "name": "Pinot Noir Rouge Fût de Chêne - Schlink",
                "slug": "pinot-noir-rouge-fut-de-chene-schlink",
                "category_slug": "vin-rouge-lux", # Link by slug
                "producer_name": "Schlink Domaine Viticole", # Link by name
                "short_description": "Pinot Noir élégant élevé en fût de chêne.",
                "full_description": "Le Domaine Schlink propose ce Pinot Noir structuré et complexe, avec des notes boisées discrètes. Un beau potentiel de garde.",
                "price": 22.50,
                "sku": "SCH-PNFDC-75",
                "stock_quantity": 40,
                "is_available": False, # Example of an unavailable product
                "is_featured": False, # Assuming not featured
                "main_image": "products/main/schlink_pinot_noir.jpg"
            }
        ]

        for product_data in products_data:
            category = categories_map.get(product_data.pop('category_slug'))
            producer = producers_map.get(product_data.pop('producer_name'))

            if not category:
                 self.stdout.write(self.style.ERROR(f"Category not found for slug: {product_data.get('category_slug')}"))
                 continue
            if not producer:
                 self.stdout.write(self.style.ERROR(f"Producer not found for name: {product_data.get('producer_name')}"))
                 continue

            product, created = VdlProduct.objects.get_or_create(
                slug=product_data['slug'],
                defaults={
                    'category': category,
                    'producer': producer,
                    **product_data # Unpack remaining data
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created product: {product.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'Product already exists: {product.name}'))


        # Create orders (keeping existing logic)
        self.stdout.write(self.style.SUCCESS('Creating orders...'))
        for i in range(5):
            order_number = f'ORDER{i}'
            if VdlOrder.objects.filter(order_number=order_number).exists():
                order = VdlOrder.objects.get(order_number=order_number)
                self.stdout.write(self.style.WARNING(f'Order already exists: {order_number}'))
            else:
                # Ensure users list is not empty before choosing
                if not users:
                    self.stdout.write(self.style.ERROR('No users available to create orders.'))
                    break # Exit loop if no users

                # Create dummy addresses if needed for the order
                shipping_address, created_shipping = VdlAddress.objects.get_or_create(
                     user=random.choice(users), # Link to a random user
                     address_line_1=f'123 Main St {i}',
                     city='Anytown',
                     country='USA',
                     defaults={'state_province': 'CA', 'postal_code': '91234', 'address_type': 'shipping'}
                )
                billing_address, created_billing = VdlAddress.objects.get_or_create(
                     user=random.choice(users), # Link to a random user
                     address_line_1=f'456 Oak Ave {i}',
                     city='Otherville',
                     country='USA',
                     defaults={'state_province': 'NY', 'postal_code': '10001', 'address_type': 'billing'}
                )


                order = VdlOrder.objects.create(
                    user=random.choice(users),
                    order_number=order_number,
                    first_name='John',
                    last_name='Doe',
                    email=f'john.doe{i}@example.com', # Use unique email
                    shipping_address=shipping_address,
                    billing_address=billing_address,
                    total_paid=random.randint(50, 500),
                )
                self.stdout.write(self.style.SUCCESS(f'Created order: {order_number}'))


            # Create order items
            self.stdout.write(self.style.SUCCESS(f'Creating order items for {order_number}...'))
            # Ensure there are products before creating order items
            all_products = VdlProduct.objects.all()
            if not all_products.exists():
                 self.stdout.write(self.style.ERROR('No products available to create order items.'))
                 # Continue to next order or break if no products at all
                 continue


            for j in range(random.randint(1, 3)): # Create 1 to 3 items per order
                product = random.choice(all_products)
                VdlOrderItem.objects.create(
                    order=order,
                    product=product,
                    price_at_purchase=product.price, # Use product's actual price
                    quantity=random.randint(1, 5)
                )
                self.stdout.write(self.style.SUCCESS(f'Created order item for {product.name} in {order_number}'))


        # Create blog post categories (keeping existing logic)
        self.stdout.write(self.style.SUCCESS('Creating blog post categories...'))
        blog_categories = []
        for i in range(2):
            name = f'Blog Category {i}'
            slug = slugify(name)
            if VdlBlogPostCategory.objects.filter(slug=slug).exists():
                blog_category = VdlBlogPostCategory.objects.get(slug=slug)
                self.stdout.write(self.style.WARNING(f'Blog category already exists: {name}'))
            else:
                blog_category = VdlBlogPostCategory.objects.create(name=name, slug=slug)
                self.stdout.write(self.style.SUCCESS(f'Created blog category: {name}'))
            blog_categories.append(blog_category)

        # Create blog posts (keeping existing logic)
        self.stdout.write(self.style.SUCCESS('Creating blog posts...'))
        # Ensure blog categories and users lists are not empty
        if not blog_categories:
             self.stdout.write(self.style.ERROR('No blog categories available to create blog posts.'))
        if not users:
             self.stdout.write(self.style.ERROR('No users available to create blog posts.'))


        if blog_categories and users:
            for i in range(5):
                title = f'Blog Post {i}'
                slug = slugify(title)
                if VdlBlogPost.objects.filter(slug=slug).exists():
                    blog_post = VdlBlogPost.objects.get(slug=slug)
                    self.stdout.write(self.style.WARNING(f'Blog post already exists: {title}'))
                else:
                    VdlBlogPost.objects.create(
                        title=title,
                        slug=slug,
                        author=random.choice(users),
                        category=random.choice(blog_categories),
                        content='Blog post content',
                        excerpt='Blog post excerpt',
                        status='published'
                    )
                    self.stdout.write(self.style.SUCCESS(f'Created blog post: {title}'))


        self.stdout.write(self.style.SUCCESS('Data population complete!'))
