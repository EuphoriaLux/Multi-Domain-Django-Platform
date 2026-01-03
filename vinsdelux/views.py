# in vinsdelux/views.py

from django.shortcuts import render, get_object_or_404
from django.utils.text import slugify
from django.core.paginator import Paginator
from django.db.models import Q
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.translation import gettext as _
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
import json
import logging
# Import only the models we actually need here
from .models import VdlCoffret, VdlAdoptionPlan, HomepageContent, VdlCategory, VdlProducer, VdlPlot, VdlPlotReservation, PlotStatus

logger = logging.getLogger(__name__)


def get_journey_base_url():
    """Get the base URL for journey images (Azure Blob in production, static in dev)."""
    return getattr(settings, 'VINSDELUX_JOURNEY_BASE_URL', '/static/vinsdelux/images/journey/')

def home(request):
    """
    Displays the homepage, featuring a selection of featured Producers.
    """
    homepage_content = HomepageContent.objects.first()

    featured_producers = VdlProducer.objects.filter(
        is_featured_on_homepage=True
    ).prefetch_related(
        'vdlcoffret_set__adoption_plans'
    )[:4]

# In your views.py or context processor

    # Journey step images served from Azure Blob Storage in production
    journey_base = get_journey_base_url()
    client_journey_steps = [
        {
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
            'image_url': f'{journey_base}step_01.png'
        },
        {
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
            'image_url': f'{journey_base}step_02.png'
        },
        {
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
            'image_url': f'{journey_base}step_03.png'
        },
        {
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
            'image_url': f'{journey_base}step_04.png'
        },
        {
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
            'image_url': f'{journey_base}step_05.png'
        }
    ]

    # Then pass this list to your template context

    context = {
        'homepage_content': homepage_content,
        'featured_producers': featured_producers,
        'client_journey_steps': client_journey_steps,
    }
    return render(request, 'vinsdelux/index.html', context)


def coffret_list(request):
    """
    Display paginated list of coffrets with filtering options.
    """
    coffrets = VdlCoffret.objects.filter(is_available=True).select_related('producer', 'category').prefetch_related('images')
    
    # Search functionality
    search_query = request.GET.get('search')
    if search_query:
        coffrets = coffrets.filter(
            Q(name__icontains=search_query) |
            Q(short_description__icontains=search_query) |
            Q(producer__name__icontains=search_query)
        )
    
    # Category filter
    category_filter = request.GET.get('category')
    if category_filter:
        coffrets = coffrets.filter(category__slug=category_filter)
    
    # Producer filter
    producer_filter = request.GET.get('producer')
    if producer_filter:
        coffrets = coffrets.filter(producer__slug=producer_filter)
    
    # Pagination
    paginator = Paginator(coffrets, 12)  # Show 12 coffrets per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Get categories and producers for filters
    categories = VdlCategory.objects.filter(is_active=True)
    producers = VdlProducer.objects.all()
    
    context = {
        'page_obj': page_obj,
        'categories': categories,
        'producers': producers,
        'search_query': search_query,
        'current_category': category_filter,
        'current_producer': producer_filter,
    }
    return render(request, 'vinsdelux/products/coffret_list.html', context)


def coffret_detail(request, slug):
    """
    Display detailed view of a specific coffret with its adoption plans.
    """
    coffret = get_object_or_404(
        VdlCoffret.objects.select_related('producer', 'category')
                          .prefetch_related('images', 'adoption_plans__images'),
        slug=slug,
        is_available=True
    )
    
    adoption_plans = coffret.adoption_plans.filter(is_available=True)
    
    # Get related coffrets from same producer
    related_coffrets = VdlCoffret.objects.filter(
        producer=coffret.producer,
        is_available=True
    ).exclude(id=coffret.id)[:4]
    
    context = {
        'coffret': coffret,
        'adoption_plans': adoption_plans,
        'related_coffrets': related_coffrets,
    }
    return render(request, 'vinsdelux/products/coffret_detail.html', context)


def adoption_plan_detail(request, slug):
    """
    Display detailed view of a specific adoption plan.
    """
    adoption_plan = get_object_or_404(
        VdlAdoptionPlan.objects.select_related('associated_coffret', 'producer', 'category')
                               .prefetch_related('images'),
        slug=slug,
        is_available=True
    )
    
    context = {
        'adoption_plan': adoption_plan,
    }
    return render(request, 'vinsdelux/products/adoption_plan_detail.html', context)


def producer_list(request):
    """
    Display list of all producers.
    """
    producers = VdlProducer.objects.prefetch_related('vdlcoffret_set').order_by('name')
    
    context = {
        'producers': producers,
    }
    return render(request, 'vinsdelux/products/producer_list.html', context)


def producer_detail(request, slug):
    """
    Display detailed view of a producer with their products.
    """
    producer = get_object_or_404(VdlProducer, slug=slug)
    coffrets = producer.vdlcoffret_set.filter(is_available=True).prefetch_related('images')
    adoption_plans = producer.vdladoptionplan_set.filter(is_available=True).prefetch_related('images')
    
    context = {
        'producer': producer,
        'coffrets': coffrets,
        'adoption_plans': adoption_plans,
    }
    return render(request, 'vinsdelux/products/producer_detail.html', context)

def plot_selector(request):
    """
    Standalone plot selector page for choosing adoption plans
    """
    from .models import VdlAdoptionPlan, VdlProducer
    
    # Fetch adoption plans from database
    adoption_plans = VdlAdoptionPlan.objects.select_related(
        'producer', 'associated_coffret', 'category'
    ).filter(
        is_available=True
    ).prefetch_related('images')
    
    # Get unique regions and producers for filters
    regions = list(adoption_plans.values_list('producer__region', flat=True).distinct())
    regions = [r for r in regions if r]  # Remove None values
    
    producers = VdlProducer.objects.filter(
        id__in=adoption_plans.values_list('producer_id', flat=True)
    ).distinct()
    
    context = {
        'adoption_plans': adoption_plans,
        'regions': regions,
        'producers': producers,
    }
    
    return render(request, 'vinsdelux/journey/plot_selector.html', context)


class EnhancedPlotSelectionView(TemplateView):
    """
    Enhanced plot selection view for luxury wine plot selection experience
    Handles both GET for display and POST for plot selections
    """
    template_name = 'vinsdelux/journey/enhanced_plot_selection.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Fetch available plots with all related data
        plots = VdlPlot.objects.select_related('producer').prefetch_related(
            'adoption_plans__images',
            'adoption_plans__associated_coffret',
            'reservations'
        ).filter(status=PlotStatus.AVAILABLE)
        
        # Get unique regions and producers for filters
        regions = list(plots.values_list('producer__region', flat=True).distinct())
        regions = [r for r in regions if r]  # Remove None values
        
        producers = VdlProducer.objects.filter(
            id__in=plots.values_list('producer_id', flat=True)
        ).distinct()
        
        # Get adoption plans associated with available plots
        adoption_plans = VdlAdoptionPlan.objects.select_related(
            'producer', 'associated_coffret', 'category'
        ).prefetch_related('images', 'plots').filter(
            is_available=True,
            plots__in=plots
        ).distinct()
        
        # Prepare plot data for frontend
        plot_data = []
        for plot in plots:
            plot_info = {
                'id': plot.id,
                'plot_identifier': plot.plot_identifier,
                'name': plot.name,
                'producer': {
                    'id': plot.producer.id,
                    'name': plot.producer.name,
                    'region': plot.producer.region,
                    'vineyard_size': plot.producer.vineyard_size,
                    'elevation': plot.producer.elevation,
                    'soil_type': plot.producer.soil_type,
                    'sun_exposure': plot.producer.sun_exposure,
                    'vineyard_features': plot.producer.vineyard_features,
                },
                'coordinates': plot.coordinates,
                'latitude': plot.latitude,
                'longitude': plot.longitude,
                'plot_size': plot.plot_size,
                'elevation': plot.elevation,
                'soil_type': plot.soil_type,
                'sun_exposure': plot.sun_exposure,
                'grape_varieties': plot.grape_varieties,
                'vine_age': plot.vine_age,
                'harvest_year': plot.harvest_year,
                'wine_profile': plot.wine_profile,
                'expected_yield': plot.expected_yield,
                'base_price': float(plot.base_price),
                'status': plot.status,
                'is_premium': plot.is_premium,
                'is_available': plot.is_available,
                'display_coordinates': plot.get_display_coordinates(),
                'primary_grape_variety': plot.get_primary_grape_variety(),
                'adoption_plans': [
                    {
                        'id': plan.id,
                        'name': plan.name,
                        'slug': plan.slug,
                        'price': float(plan.price),
                        'duration_months': plan.duration_months,
                        'coffrets_per_year': plan.coffrets_per_year,
                        'includes_visit': plan.includes_visit,
                        'includes_medallion': plan.includes_medallion,
                        'includes_club_membership': plan.includes_club_membership,
                        'short_description': plan.short_description,
                        'main_image': plan.main_image.url if plan.main_image else None,
                    }
                    for plan in plot.adoption_plans.filter(is_available=True)
                ]
            }
            plot_data.append(plot_info)
        
        # Journey steps for progress indicator
        journey_steps = [
            {
                'step': 1,
                'title': _('Plot Selection'),
                'description': _('Choose your vineyard plot'),
                'is_current': True,
                'is_completed': False,
            },
            {
                'step': 2,
                'title': _('Adoption Plan'),
                'description': _('Select your plan'),
                'is_current': False,
                'is_completed': False,
            },
            {
                'step': 3,
                'title': _('Confirmation'),
                'description': _('Confirm adoption'),
                'is_current': False,
                'is_completed': False,
            }
        ]
        
        context.update({
            'plots': plots,
            'plot_data': json.dumps(plot_data),  # JSON for JavaScript
            'adoption_plans': adoption_plans,
            'regions': regions,
            'producers': producers,
            'journey_steps': journey_steps,
            'page_title': _('Select Your Vineyard Plot'),
            'page_description': _('Choose from our exclusive collection of premium vineyard plots across renowned wine regions.'),
        })
        
        return context
    
    def post(self, request, *args, **kwargs):
        """
        Handle plot selection submissions
        """
        try:
            data = json.loads(request.body)
            selected_plots = data.get('selected_plots', [])
            user_notes = data.get('notes', '')
            
            if not selected_plots:
                return JsonResponse({
                    'success': False,
                    'error': _('No plots selected')
                }, status=400)
            
            # Validate plots exist and are available
            valid_plots = VdlPlot.objects.filter(
                id__in=selected_plots,
                status=PlotStatus.AVAILABLE
            )
            
            if len(valid_plots) != len(selected_plots):
                return JsonResponse({
                    'success': False,
                    'error': _('Some selected plots are no longer available')
                }, status=400)
            
            # Store selection in session for logged-in users or return for guest users
            if request.user.is_authenticated:
                # Clear existing reservations for this user
                VdlPlotReservation.objects.filter(user=request.user).delete()
                
                # Create new reservations
                for plot in valid_plots:
                    VdlPlotReservation.objects.create(
                        plot=plot,
                        user=request.user,
                        expires_at=timezone.now() + timedelta(hours=24),  # 24-hour reservation
                        notes=user_notes,
                        session_data={
                            'selection_timestamp': timezone.now().isoformat(),
                            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        }
                    )
            
            # Store in session regardless of authentication status
            request.session['selected_plots'] = selected_plots
            request.session['plot_selection_timestamp'] = timezone.now().isoformat()
            request.session['plot_selection_notes'] = user_notes
            
            return JsonResponse({
                'success': True,
                'message': _('Plots selected successfully'),
                'selected_count': len(valid_plots),
                'next_step_url': reverse('vinsdelux:adoption_plan_selection'),
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': _('Invalid request format')
            }, status=400)
        except Exception as e:
            logger.error(f"Error in plot selection: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': _('An error occurred while processing your selection')
            }, status=500)


# --- REST API Views ---

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from datetime import timedelta
from .serializers import (
    VdlPlotSerializer, VdlPlotListSerializer, VdlPlotReservationSerializer,
    PlotReservationCreateSerializer, PlotAvailabilitySerializer,
    PlotSelectionSerializer
)


class PlotListAPIView(generics.ListAPIView):
    """
    API endpoint for listing available plots
    GET /api/plots/
    """
    serializer_class = VdlPlotListSerializer
    permission_classes = [AllowAny]
    
    def get_queryset(self):
        """Filter plots based on query parameters"""
        queryset = VdlPlot.objects.select_related('producer').prefetch_related(
            'adoption_plans'
        ).filter(status=PlotStatus.AVAILABLE)
        
        # Filter by region
        region = self.request.query_params.get('region')
        if region:
            queryset = queryset.filter(producer__region__icontains=region)
        
        # Filter by producer
        producer_id = self.request.query_params.get('producer_id')
        if producer_id:
            queryset = queryset.filter(producer_id=producer_id)
        
        # Filter by price range
        min_price = self.request.query_params.get('min_price')
        max_price = self.request.query_params.get('max_price')
        if min_price:
            try:
                queryset = queryset.filter(base_price__gte=float(min_price))
            except ValueError:
                pass
        if max_price:
            try:
                queryset = queryset.filter(base_price__lte=float(max_price))
            except ValueError:
                pass
        
        # Filter by grape variety
        grape_variety = self.request.query_params.get('grape_variety')
        if grape_variety:
            queryset = queryset.filter(grape_varieties__contains=[grape_variety])
        
        # Filter premium plots only
        premium_only = self.request.query_params.get('premium_only')
        if premium_only == 'true':
            queryset = queryset.filter(is_premium=True)
        
        return queryset.order_by('producer__name', 'plot_identifier')


class PlotDetailAPIView(generics.RetrieveAPIView):
    """
    API endpoint for retrieving plot details
    GET /api/plots/<id>/
    """
    serializer_class = VdlPlotSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'
    
    def get_queryset(self):
        return VdlPlot.objects.select_related('producer').prefetch_related(
            'adoption_plans__associated_coffret',
            'adoption_plans__category',
            'reservations'
        )


class PlotAvailabilityAPIView(APIView):
    """
    API endpoint for checking plot availability
    GET /api/plots/availability/
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get availability statistics for all plots"""
        total_plots = VdlPlot.objects.all()
        
        availability_data = {
            'available_count': total_plots.filter(status=PlotStatus.AVAILABLE).count(),
            'reserved_count': total_plots.filter(status=PlotStatus.RESERVED).count(),
            'adopted_count': total_plots.filter(status=PlotStatus.ADOPTED).count(),
            'total_count': total_plots.count(),
            'last_updated': timezone.now()
        }
        
        # Include available plots if requested
        include_plots = request.query_params.get('include_plots', '').lower() == 'true'
        if include_plots:
            available_plots = total_plots.filter(status=PlotStatus.AVAILABLE).select_related('producer')[:20]
            availability_data['available_plots'] = VdlPlotListSerializer(available_plots, many=True).data
        else:
            availability_data['available_plots'] = []
        
        serializer = PlotAvailabilitySerializer(availability_data)
        return Response(serializer.data)


class PlotReservationAPIView(APIView):
    """
    API endpoint for creating plot reservations
    POST /api/plots/reserve/
    """
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        """Create a new plot reservation"""
        serializer = PlotReservationCreateSerializer(data=request.data)
        if serializer.is_valid():
            plot_ids = serializer.validated_data['plot_ids']
            notes = serializer.validated_data.get('notes', '')
            adoption_plan_id = serializer.validated_data.get('adoption_plan_id')
            
            # Get valid plots
            plots = VdlPlot.objects.filter(id__in=plot_ids, status=PlotStatus.AVAILABLE)
            
            # Clear existing reservations for this user
            VdlPlotReservation.objects.filter(user=request.user).delete()
            
            # Get adoption plan if specified
            adoption_plan = None
            if adoption_plan_id:
                try:
                    adoption_plan = VdlAdoptionPlan.objects.get(
                        id=adoption_plan_id,
                        is_available=True
                    )
                except VdlAdoptionPlan.DoesNotExist:
                    return Response({
                        'error': 'Adoption plan not found or not available'
                    }, status=status.HTTP_400_BAD_REQUEST)
            
            # Create new reservations
            reservations = []
            for plot in plots:
                reservation = VdlPlotReservation.objects.create(
                    plot=plot,
                    user=request.user,
                    adoption_plan=adoption_plan,
                    expires_at=timezone.now() + timedelta(hours=24),
                    notes=notes,
                    session_data={
                        'reservation_timestamp': timezone.now().isoformat(),
                        'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        'ip_address': request.META.get('REMOTE_ADDR', ''),
                    }
                )
                reservations.append(reservation)
            
            # Update plot status to reserved
            plots.update(status=PlotStatus.RESERVED)
            
            # Return reservation details
            reservation_data = VdlPlotReservationSerializer(reservations, many=True).data
            
            return Response({
                'success': True,
                'message': f'{len(reservations)} plot(s) reserved successfully',
                'reservations': reservation_data,
                'expires_at': reservations[0].expires_at if reservations else None
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PlotSelectionAPIView(APIView):
    """
    API endpoint for plot selection (session-based for guests, reservation-based for authenticated users)
    GET /api/plots/selection/ - Get current selection
    POST /api/plots/selection/ - Update selection
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """Get current plot selection"""
        if request.user.is_authenticated:
            # Get reservations for authenticated users
            reservations = VdlPlotReservation.objects.filter(
                user=request.user,
                is_confirmed=False
            ).select_related('plot__producer').prefetch_related('plot__adoption_plans')
            
            plots_data = []
            for reservation in reservations:
                plot_data = VdlPlotListSerializer(reservation.plot).data
                plot_data['reservation_id'] = reservation.id
                plot_data['reserved_at'] = reservation.reserved_at
                plot_data['expires_at'] = reservation.expires_at
                plot_data['is_expired'] = reservation.is_expired
                plots_data.append(plot_data)
            
            return Response({
                'selected_plots': plots_data,
                'selection_count': len(plots_data),
                'source': 'reservations'
            })
        else:
            # Get from session for guest users
            selected_plot_ids = request.session.get('selected_plots', [])
            if selected_plot_ids:
                plots = VdlPlot.objects.filter(id__in=selected_plot_ids).select_related('producer')
                plots_data = VdlPlotListSerializer(plots, many=True).data
                
                return Response({
                    'selected_plots': plots_data,
                    'selection_count': len(plots_data),
                    'source': 'session',
                    'selection_timestamp': request.session.get('plot_selection_timestamp')
                })
            else:
                return Response({
                    'selected_plots': [],
                    'selection_count': 0,
                    'source': 'session'
                })
    
    def post(self, request):
        """Update plot selection"""
        serializer = PlotSelectionSerializer(data=request.data)
        if serializer.is_valid():
            selected_plots = serializer.validated_data['selected_plots']
            user_notes = serializer.validated_data.get('user_notes', '')
            
            # Validate plots are available
            plots = VdlPlot.objects.filter(id__in=selected_plots, status=PlotStatus.AVAILABLE)
            if len(plots) != len(selected_plots):
                return Response({
                    'error': 'Some selected plots are not available'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            if request.user.is_authenticated:
                # Create or update reservations for authenticated users
                VdlPlotReservation.objects.filter(user=request.user, is_confirmed=False).delete()
                
                reservations = []
                for plot in plots:
                    reservation = VdlPlotReservation.objects.create(
                        plot=plot,
                        user=request.user,
                        expires_at=timezone.now() + timedelta(hours=24),
                        notes=user_notes,
                        session_data={
                            'selection_timestamp': timezone.now().isoformat(),
                            'user_agent': request.META.get('HTTP_USER_AGENT', ''),
                        }
                    )
                    reservations.append(reservation)
                
                return Response({
                    'success': True,
                    'message': f'{len(reservations)} plot(s) selected successfully',
                    'selection_count': len(reservations),
                    'reservations': VdlPlotReservationSerializer(reservations, many=True).data
                }, status=status.HTTP_201_CREATED)
            else:
                # Store in session for guest users
                request.session['selected_plots'] = selected_plots
                request.session['plot_selection_timestamp'] = timezone.now().isoformat()
                request.session['plot_selection_notes'] = user_notes
                
                plots_data = VdlPlotListSerializer(plots, many=True).data
                
                return Response({
                    'success': True,
                    'message': f'{len(plots)} plot(s) selected successfully',
                    'selection_count': len(plots),
                    'selected_plots': plots_data,
                    'source': 'session'
                })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def enhanced_plot_selector(request):
    """
    Backward compatibility wrapper for the enhanced plot selector
    """
    view = EnhancedPlotSelectionView()
    view.request = request
    return view.dispatch(request)


def journey_interactive_form(request):
    """
    Interactive gamified journey form for VinsDelux customer experience.
    """
    import json
    from .models import VdlAdoptionPlan, VdlProducer
    
    # Client journey steps data for the interactive form (Azure Blob in production)
    journey_base = get_journey_base_url()
    client_journey_steps = [
        {
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
            'image_url': f'{journey_base}step_01.png'
        },
        {
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
            'image_url': f'{journey_base}step_02.png'
        },
        {
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
            'image_url': f'{journey_base}step_03.png'
        },
        {
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
            'image_url': f'{journey_base}step_04.png'
        },
        {
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
            'image_url': f'{journey_base}step_05.png'
        }
    ]

    # Fetch adoption plans from database with related producer data
    adoption_plans = VdlAdoptionPlan.objects.select_related(
        'producer', 'associated_coffret', 'category'
    ).filter(
        is_available=True
    ).prefetch_related('images')
    
    # Convert adoption plans to plot data for the frontend
    plot_data = []
    for plan in adoption_plans:
        plot_item = {
            'id': f'plot-{plan.id}',
            'plan_id': plan.id,
            'name': plan.name,
            'producer_name': plan.producer.name if plan.producer else 'Unknown Producer',
            'region': plan.producer.region if plan.producer else 'Unknown Region',
            'price': str(plan.price),
            'duration_months': plan.duration_months,
            'coffrets_per_year': plan.coffrets_per_year,
            'description': plan.short_description,
            'full_description': plan.full_description,
            'includes_visit': plan.includes_visit,
            'visit_details': plan.visit_details,
            'includes_medallion': plan.includes_medallion,
            'includes_club_membership': plan.includes_club_membership,
            'avant_premiere_price': str(plan.avant_premiere_price) if plan.avant_premiere_price else None,
            'welcome_kit_description': plan.welcome_kit_description,
            'coffret_name': plan.associated_coffret.name if plan.associated_coffret else None,
            'category': plan.category.get_name_display() if plan.category else None,
            'main_image': plan.main_image.url if plan.main_image else None,
            'is_available': plan.is_available,
        }
        plot_data.append(plot_item)
    
    context = {
        'client_journey_steps': client_journey_steps,
        'adoption_plans': adoption_plans,
        'plot_data': json.dumps(plot_data),  # Convert to JSON string for JavaScript
    }
    response = render(request, 'vinsdelux/journey/interactive_form.html', context)
    # Allow iframe embedding for test purposes
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response

def journey_test_runner(request):
    """
    Test runner page for the futuristic journey functionality.
    Includes comprehensive testing tools and validation.
    """
    # Same client journey steps as used in the main site (Azure Blob in production)
    journey_base = get_journey_base_url()
    client_journey_steps = [
        {
            'step': '01',
            'title': 'Plot Selection',
            'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
            'image_url': f'{journey_base}step_01.png'
        },
        {
            'step': '02',
            'title': 'Personalize Your Wine',
            'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
            'image_url': f'{journey_base}step_02.png'
        },
        {
            'step': '03',
            'title': 'Follow the Production',
            'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
            'image_url': f'{journey_base}step_03.png'
        },
        {
            'step': '04',
            'title': 'Receive and Taste',
            'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
            'image_url': f'{journey_base}step_04.png'
        },
        {
            'step': '05',
            'title': 'Create Your Legacy',
            'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
            'image_url': f'{journey_base}step_05.png'
        }
    ]

    context = {
        'client_journey_steps': client_journey_steps,
    }
    return render(request, 'vinsdelux/testing/test_runner.html', context)

def journey_step_plot_selection(request):
    """
    Landing page for Step 1: Plot Selection
    """
    step_data = {
        'step': '01',
        'title': 'Plot Selection',
        'description': 'Choose the vineyard plot that matches your preferences and vision. Each plot offers a unique terroir and a story to tell.',
        'image_url': f'{get_journey_base_url()}step_01.png',
        'detailed_description': 'With VinsDeLux, you don\'t just buy wine - you adopt a piece of vineyard history. Our exclusive plot selection process connects you directly to the land, giving you ownership rights that traditional wine purchases can\'t offer. Unlike buying bottles from a store, wine adoption through VinsDeLux means you\'re investing in the entire winemaking process from soil to bottle.',
        'main_benefits': [
            {
                'title': 'True Ownership Experience',
                'description': 'Unlike traditional wine buying, you actually own a designated plot of vineyard. This gives you legal rights, visitation privileges, and a genuine connection to your wine\'s origin.',
                'icon': 'fas fa-certificate'
            },
            {
                'title': 'Investment Potential',
                'description': 'Wine adoption appreciates in value over time. Your plot and its production become more valuable as the vineyard matures and gains recognition.',
                'icon': 'fas fa-chart-line'
            },
            {
                'title': 'Exclusive Access',
                'description': 'VinsDeLux provides access to premier vineyard plots that are not available through traditional wine retailers. These are often family-owned estates with centuries of history.',
                'icon': 'fas fa-key'
            }
        ],
        'features': [
            'Over 250 premium vineyard plots across Bordeaux, Burgundy, Champagne, and Tuscany',
            'Legal plot ownership documentation with GPS coordinates and boundary mapping',
            'Comprehensive terroir analysis including soil pH, mineral content, and drainage reports',
            'Historical yield data dating back 20+ years with quality ratings',
            'Personal viticulturist consultation to match your taste preferences with terroir characteristics',
            'Annual plot valuation reports showing appreciation and market comparisons',
            'Exclusive access to harvest events and winemaker dinners at your vineyard'
        ],
        'benefits': [
            'Own a piece of wine history - something you can never get from retail wine purchases',
            'Direct relationship with vineyard owners and winemakers',
            'Guaranteed authenticity and provenance tracking from soil to bottle',
            'Investment diversification into agricultural real estate and luxury goods',
            'Ability to pass down your vineyard plot to future generations',
            'Priority access to limited production and vintage wines from your plot'
        ],
        'why_vinsdelux': {
            'title': 'Why Choose VinsDeLux Over Traditional Wine Buying?',
            'points': [
                'Traditional wine retailers sell you finished products - VinsDeLux gives you ownership of the production process',
                'Retail wine prices include multiple markups - direct adoption eliminates middlemen costs',
                'Store-bought wines have no personal connection - your adopted plot creates a lifelong relationship',
                'Commercial wines are mass-produced - your plot produces wine exclusively for you and select adopters'
            ]
        },
        'testimonial': {
            'text': 'After 10 years of buying expensive wines from shops, adopting through VinsDeLux changed everything. Now I own part of a Burgundy vineyard, and my wine collection has tripled in value while giving me incredible experiences.',
            'author': 'Jean-Pierre M., VinsDeLux Member since 2019'
        }
    }
    
    context = {
        'step_data': step_data,
        'current_step': 1,
        'total_steps': 5,
        'next_step_url': 'vinsdelux:journey_personalize_wine',
        'next_step_title': 'Personalize Your Wine'
    }
    return render(request, 'vinsdelux/journey/step_detail.html', context)

def journey_step_personalize_wine(request):
    """
    Landing page for Step 2: Personalize Your Wine
    """
    step_data = {
        'step': '02',
        'title': 'Personalize Your Wine',
        'description': 'Collaborate with our expert winemakers to personalize every aspect of your wine, from the grape variety to the winemaking techniques.',
        'image_url': f'{get_journey_base_url()}step_02.png',
        'detailed_description': 'This is where VinsDeLux truly differentiates itself from any wine purchasing experience. While retail wines are mass-produced for broad appeal, your VinsDeLux adoption allows you to work directly with master winemakers to create a wine that\'s uniquely yours. This level of personalization is impossible with traditional wine buying.',
        'main_benefits': [
            {
                'title': 'Master Winemaker Partnership',
                'description': 'Work directly with award-winning winemakers who have crafted wines for Michelin-starred restaurants and royal families. This access is exclusive to VinsDeLux adopters.',
                'icon': 'fas fa-award'
            },
            {
                'title': 'Unique Wine Creation',
                'description': 'Your wine becomes a one-of-a-kind creation that cannot be purchased anywhere else. This exclusivity increases both personal satisfaction and market value.',
                'icon': 'fas fa-palette'
            },
            {
                'title': 'Educational Journey',
                'description': 'Learn advanced winemaking techniques directly from masters. This knowledge makes you a more sophisticated wine connoisseur and enhances your appreciation.',
                'icon': 'fas fa-graduation-cap'
            }
        ],
        'features': [
            'Private consultation sessions with master winemakers (avg. 25+ years experience)',
            'Choice between organic, biodynamic, or traditional farming methodologies',
            'Custom grape variety selection including rare heritage varietals',
            'Personalized fermentation timing and temperature control',
            'Exclusive barrel selection from French, American, or Hungarian oak cooperages',
            'Custom blend ratios designed to your palate preferences',
            'Personalized bottle design, cork selection, and label creation',
            'pH and tannin level adjustments based on your taste profile',
            'Access to experimental winemaking techniques not used in commercial production'
        ],
        'benefits': [
            'Create a wine that perfectly matches your personal taste preferences',
            'Learn from master winemakers who typically only work with premium estates',
            'Access to rare grape varietals and experimental techniques',
            'Your personalized wine becomes a conversation piece and heirloom',
            'Increase your wine knowledge to sommelier-level understanding',
            'Build relationships with renowned winemakers in your region'
        ],
        'why_vinsdelux': {
            'title': 'The VinsDeLux Personalization Advantage',
            'points': [
                'Retail wines are made for mass appeal - yours is crafted for your specific palate',
                'Commercial winemakers follow rigid formulas - our masters have creative freedom with your wine',
                'Store-bought wines use standard techniques - you get access to innovative methods',
                'Traditional wine buying offers no customization - VinsDeLux gives you complete control'
            ]
        },
        'testimonial': {
            'text': 'Working with Master Winemaker François Dubois to create my personalized Chardonnay was incredible. The wine perfectly captures my love for crisp minerality with subtle oak. Friends are amazed when I tell them it\'s MY wine.',
            'author': 'Sarah L., Personalized Wine Creator, VinsDeLux'
        }
    }
    
    context = {
        'step_data': step_data,
        'current_step': 2,
        'total_steps': 5,
        'prev_step_url': 'vinsdelux:journey_plot_selection',
        'prev_step_title': 'Plot Selection',
        'next_step_url': 'vinsdelux:journey_follow_production',
        'next_step_title': 'Follow the Production'
    }
    return render(request, 'vinsdelux/journey/step_detail.html', context)

def journey_step_follow_production(request):
    """
    Landing page for Step 3: Follow the Production
    """
    step_data = {
        'step': '03',
        'title': 'Follow the Production',
        'description': 'Receive regular updates on the growth of your vine and the winemaking process, complete with detailed photos and reports.',
        'image_url': f'{get_journey_base_url()}step_03.png',
        'detailed_description': 'VinsDeLux offers unprecedented transparency that no traditional wine retailer can match. When you buy wine from a store, you know nothing about its production journey. With VinsDeLux adoption, you become part of every decision, witnessing your wine\'s creation from bud break to bottling. This transparency ensures quality and creates an emotional connection impossible with commercial wines.',
        'main_benefits': [
            {
                'title': 'Complete Production Transparency',
                'description': 'Unlike retail wines with unknown production histories, you witness every step of your wine\'s creation. This transparency guarantees quality and authenticity.',
                'icon': 'fas fa-eye'
            },
            {
                'title': 'Quality Assurance',
                'description': 'Real-time monitoring allows immediate intervention if issues arise. Your wine receives attention that mass-produced commercial wines never get.',
                'icon': 'fas fa-shield-alt'
            },
            {
                'title': 'Educational Value',
                'description': 'Learn viticulture and winemaking from experts. This knowledge makes you a more sophisticated wine lover and adds value to your investment.',
                'icon': 'fas fa-brain'
            }
        ],
        'features': [
            'Weekly production updates during critical growing periods',
            'Professional vineyard photography documenting your plot\'s development',
            'Real-time weather monitoring and climate impact analysis',
            'Soil moisture, pH, and nutrient level tracking with scientific reports',
            'Live streaming cameras during key events (pruning, harvest, pressing)',
            'Quality testing results for sugar levels, acidity, and tannin development',
            'Harvest predictions with optimal picking date recommendations',
            'Fermentation progress tracking with daily temperature and activity logs',
            'Barrel aging reports including tasting notes from the winemaker',
            'Bottling process documentation with quality control verification'
        ],
        'benefits': [
            'Complete visibility into every production decision affecting your wine',
            'Early detection and prevention of potential quality issues',
            'Documentation creating a valuable provenance record for your wine',
            'Educational content that enhances your wine appreciation and knowledge',
            'Emotional connection to your wine that increases personal satisfaction',
            'Professional-grade monitoring that ensures optimal wine quality'
        ],
        'why_vinsdelux': {
            'title': 'VinsDeLux Transparency vs. Traditional Wine Buying',
            'points': [
                'Retail wines offer zero production visibility - VinsDeLux provides complete transparency',
                'Commercial wines use mass production shortcuts - your wine gets individualized attention',
                'Store-bought wines have unknown storage histories - you control every aspect of your wine\'s journey',
                'Traditional purchases offer no quality guarantees - our monitoring ensures optimal production'
            ]
        },
        'testimonial': {
            'text': 'Watching my Pinot Noir develop through the VinsDeLux monitoring system was fascinating. When I serve it to guests, I can tell them exactly how it was made. The documentation alone makes it a treasured possession.',
            'author': 'Dr. Michael R., Wine Adopter and Medical Professional'
        }
    }
    
    context = {
        'step_data': step_data,
        'current_step': 3,
        'total_steps': 5,
        'prev_step_url': 'vinsdelux:journey_personalize_wine',
        'prev_step_title': 'Personalize Your Wine',
        'next_step_url': 'vinsdelux:journey_receive_taste',
        'next_step_title': 'Receive and Taste'
    }
    return render(request, 'vinsdelux/journey/step_detail.html', context)

def journey_step_receive_taste(request):
    """
    Landing page for Step 4: Receive and Taste
    """
    step_data = {
        'step': '04',
        'title': 'Receive and Taste',
        'description': 'Your personalized bottles are delivered directly to your home, ready to be tasted and shared with loved ones.',
        'image_url': f'{get_journey_base_url()}step_04.png',
        'detailed_description': 'This is the moment that separates VinsDeLux from every wine purchase you\'ve ever made. When your personalized bottles arrive, you\'re not just receiving wine - you\'re receiving the culmination of your journey, your investment, and your personal creation. Each bottle carries your story, your choices, and your connection to the land. This emotional and financial value is impossible to achieve with retail wine purchases.',
        'main_benefits': [
            {
                'title': 'Premium Delivery Experience',
                'description': 'White-glove delivery service ensures your wine arrives in perfect condition. Temperature-controlled transport and specialized packaging protect your investment.',
                'icon': 'fas fa-truck'
            },
            {
                'title': 'Personalized Wine Collection',
                'description': 'Each bottle is uniquely yours with personalized labels and documentation. This creates a valuable collection that appreciates over time.',
                'icon': 'fas fa-wine-bottle'
            },
            {
                'title': 'Investment-Grade Quality',
                'description': 'Your wine is produced to investment standards with proper aging potential. Unlike commercial wines, yours can appreciate significantly in value.',
                'icon': 'fas fa-gem'
            }
        ],
        'features': [
            'Temperature-controlled delivery with specialized wine transport vehicles',
            'Custom-designed bottles with your personalized label and vintage year',
            'Premium wooden gift boxes with your name and plot information engraved',
            'Comprehensive tasting notes written personally by your winemaker',
            'Certificate of authenticity with plot GPS coordinates and legal ownership documentation',
            'Professional wine evaluation and scoring from certified sommeliers',
            'Optimal cellaring and aging recommendations for maximum value appreciation',
            'Exclusive food pairing guide created by Michelin-starred chefs',
            'Digital wine passport with blockchain verification for authenticity',
            'Insurance valuation for your wine collection with yearly updates'
        ],
        'benefits': [
            'Guaranteed perfect condition delivery with full insurance coverage',
            'Unique bottles that cannot be purchased anywhere else in the world',
            'Professional documentation that increases your wine\'s market value',
            'Educational materials that enhance your wine knowledge and appreciation',
            'Investment-grade wine with strong appreciation potential',
            'Perfect gifts that create lasting memories and conversations'
        ],
        'why_vinsdelux': {
            'title': 'VinsDeLux Delivery vs. Traditional Wine Purchasing',
            'points': [
                'Retail wine delivery is basic shipping - VinsDeLux provides luxury white-glove service',
                'Store-bought wines come in standard bottles - yours are personalized masterpieces',
                'Commercial wines have no documentation - your bottles include comprehensive provenance',
                'Traditional wine purchases depreciate after opening - yours appreciate as collectibles'
            ]
        },
        'testimonial': {
            'text': 'When my first bottles arrived, I actually got emotional. Seeing my name on labels of wine from MY vineyard plot was incredible. The delivery was like receiving art, not just wine. My collection has doubled in value since.',
            'author': 'Emma T., Wine Collector and VinsDeLux Adopter'
        }
    }
    
    context = {
        'step_data': step_data,
        'current_step': 4,
        'total_steps': 5,
        'prev_step_url': 'vinsdelux:journey_follow_production',
        'prev_step_title': 'Follow the Production',
        'next_step_url': 'vinsdelux:journey_create_legacy',
        'next_step_title': 'Create Your Legacy'
    }
    return render(request, 'vinsdelux/journey/step_detail.html', context)

def journey_step_create_legacy(request):
    """
    Landing page for Step 5: Create Your Legacy
    """
    step_data = {
        'step': '05',
        'title': 'Create Your Legacy',
        'description': 'Your wine becomes a family legacy, a unique expression of your passion for wine, and an investment in the future.',
        'image_url': f'{get_journey_base_url()}step_05.png',
        'detailed_description': 'This is where VinsDeLux transcends traditional wine purchasing to become a generational investment. While retail wines are consumed and forgotten, your VinsDeLux adoption creates a lasting legacy that grows in value and meaning over time. You\'re not just buying wine - you\'re establishing a family heritage that can be passed down for generations, creating stories and traditions that retail wine purchases simply cannot match.',
        'main_benefits': [
            {
                'title': 'Generational Wealth Building',
                'description': 'Your vineyard adoption becomes a valuable asset that appreciates over time. Fine wine consistently outperforms traditional investments, with top vintages appreciating 200-500% over decades.',
                'icon': 'fas fa-coins'
            },
            {
                'title': 'Family Heritage Creation',
                'description': 'Establish a meaningful family tradition that connects generations. Your wine becomes part of your family story, shared at weddings, celebrations, and special moments.',
                'icon': 'fas fa-users'
            },
            {
                'title': 'Exclusive Legacy Status',
                'description': 'Join an elite group of wine legacy holders with exclusive access to rare events, private tastings, and investment opportunities not available to the general public.',
                'icon': 'fas fa-crown'
            }
        ],
        'features': [
            'Perpetual access rights to your vineyard plot for annual harvests',
            'Legacy certification documenting your family\'s wine heritage',
            'Annual vintage production from your dedicated terroir',
            'Professional investment tracking and valuation services',
            'Generational transfer planning with legal documentation',
            'Exclusive VinsDeLux Legacy Club membership with networking events',
            'Priority access to rare and limited edition wine opportunities',
            'Professional wine storage facilities with climate control',
            'Annual legacy holder gatherings at premiere wine regions',
            'Family crest integration into bottle design for future generations'
        ],
        'benefits': [
            'Build a valuable wine collection that appreciates faster than traditional investments',
            'Create meaningful family traditions and stories that last generations',
            'Establish your family name in the world of fine wine collecting',
            'Access exclusive investment opportunities in the wine industry',
            'Leave a tangible, valuable inheritance that grows over time',
            'Network with other successful wine legacy families and investors'
        ],
        'why_vinsdelux': {
            'title': 'VinsDeLux Legacy vs. Traditional Wine Collecting',
            'points': [
                'Retail wine collecting requires constant purchasing - VinsDeLux provides ongoing production from your plot',
                'Commercial wines have limited appreciation potential - vineyard ownership grows in value consistently',
                'Traditional collecting lacks personal connection - your legacy has your story and choices embedded',
                'Store-bought wines can be replicated by anyone - your legacy wine is uniquely yours forever'
            ]
        },
        'investment_data': {
            'title': 'Investment Performance of VinsDeLux Adoptions',
            'statistics': [
                {'metric': 'Average Annual Appreciation', 'value': '12-18%'},
                {'metric': 'Legacy Members Since 2015', 'value': '2,847'},
                {'metric': 'Average Portfolio Value After 10 Years', 'value': '€125,000'},
                {'metric': 'Generational Transfers Completed', 'value': '342'}
            ]
        },
        'testimonial': {
            'text': 'Starting my VinsDeLux legacy in 2018 was the best investment decision I ever made. My children are now part of the story, and my wine collection has become a valuable family asset worth more than our car. It\'s not just wine - it\'s our family\'s future.',
            'author': 'Robert K., Legacy Member and Father of Three'
        }
    }
    
    context = {
        'step_data': step_data,
        'current_step': 5,
        'total_steps': 5,
        'prev_step_url': 'vinsdelux:journey_receive_taste',
        'prev_step_title': 'Receive and Taste',
        'is_final_step': True
    }
    return render(request, 'vinsdelux/journey/step_detail.html', context)

def api_adoption_plans(request):
    """
    API endpoint to fetch adoption plans as JSON for dynamic updates
    """
    from django.http import JsonResponse
    from django.db.models import Q
    from .models import VdlAdoptionPlan
    import traceback
    
    try:
        # Get filter parameters from request
        producer_id = request.GET.get('producer_id')
        region = request.GET.get('region')
        min_price = request.GET.get('min_price')
        max_price = request.GET.get('max_price')
        category = request.GET.get('category')
        includes_visit = request.GET.get('includes_visit')
        includes_medallion = request.GET.get('includes_medallion')
        includes_club = request.GET.get('includes_club')
        search = request.GET.get('search')
        
        # Build query
        plans = VdlAdoptionPlan.objects.select_related(
            'producer', 'associated_coffret', 'category'
        ).prefetch_related('images').filter(is_available=True)
        
        # Apply filters
        if producer_id:
            plans = plans.filter(producer_id=producer_id)
        if region:
            plans = plans.filter(producer__region__icontains=region)
        if min_price:
            try:
                plans = plans.filter(price__gte=float(min_price))
            except ValueError:
                pass
        if max_price:
            try:
                plans = plans.filter(price__lte=float(max_price))
            except ValueError:
                pass
        if category:
            plans = plans.filter(category__name=category)
        if includes_visit == 'true':
            plans = plans.filter(includes_visit=True)
        if includes_medallion == 'true':
            plans = plans.filter(includes_medallion=True)
        if includes_club == 'true':
            plans = plans.filter(includes_club_membership=True)
        if search:
            plans = plans.filter(
                Q(name__icontains=search) |
                Q(short_description__icontains=search) |
                Q(producer__name__icontains=search) |
                Q(producer__region__icontains=search)
            )
        
        # Get available filters for the UI
        all_regions = list(VdlAdoptionPlan.objects.filter(
            is_available=True, producer__isnull=False
        ).values_list('producer__region', flat=True).distinct())
        all_regions = [r for r in all_regions if r]  # Remove None values
        
        all_producers = list(VdlAdoptionPlan.objects.filter(
            is_available=True, producer__isnull=False
        ).values_list('producer__id', 'producer__name').distinct())
        
        # Convert to JSON-serializable format
        data = []
        for plan in plans:
            item = {
                'id': plan.id,
                'name': plan.name,
                'slug': plan.slug,
                'producer': {
                    'id': plan.producer.id if plan.producer else None,
                    'name': plan.producer.name if plan.producer else 'Unknown',
                    'region': plan.producer.region if plan.producer else 'Unknown',
                    'vineyard_size': plan.producer.vineyard_size if plan.producer else None,
                    'elevation': plan.producer.elevation if plan.producer else None,
                    'soil_type': plan.producer.soil_type if plan.producer else None,
                    'sun_exposure': plan.producer.sun_exposure if plan.producer else None,
                    'map_x': plan.producer.map_x_position if plan.producer else 50,
                    'map_y': plan.producer.map_y_position if plan.producer else 50,
                    'vineyard_features': plan.producer.vineyard_features if plan.producer else [],
                },
                'price': float(plan.price),
                'duration_months': plan.duration_months,
                'coffrets_per_year': plan.coffrets_per_year,
                'description': plan.short_description,
                'full_description': plan.full_description,
                'features': {
                    'includes_visit': plan.includes_visit,
                    'includes_medallion': plan.includes_medallion,
                    'includes_club_membership': plan.includes_club_membership,
                },
                'visit_details': plan.visit_details,
                'welcome_kit': plan.welcome_kit_description,
                'images': plan.get_images_or_defaults(),
                'coffret': {
                    'name': plan.associated_coffret.name if plan.associated_coffret else None,
                    'price': float(plan.associated_coffret.price) if plan.associated_coffret and plan.associated_coffret.price else None,
                },
                'category': plan.category.get_name_display() if plan.category else None,
                'image_url': plan.main_image.url if plan.main_image else None,
                'avant_premiere_price': float(plan.avant_premiere_price) if plan.avant_premiere_price else None,
            }
            data.append(item)
    
        return JsonResponse({
            'adoption_plans': data,
            'filters': {
                'regions': all_regions,
                'producers': [{'id': p[0], 'name': p[1]} for p in all_producers],
                'total_count': len(data)
            }
        })
    except Exception as e:
        # Log the error for debugging
        print(f"Error in api_adoption_plans: {str(e)}")
        print(traceback.format_exc())
        
        # Return a proper JSON error response
        return JsonResponse({
            'error': 'Failed to load adoption plans',
            'message': str(e) if request.GET.get('debug') else 'An error occurred while loading the data',
            'adoption_plans': [],
            'filters': {
                'regions': [],
                'producers': [],
                'total_count': 0
            }
        }, status=500)

def journey_test(request):
    """
    Test suite for debugging the interactive journey game
    """
    response = render(request, 'vinsdelux/testing/test_suite.html')
    # Allow iframe for testing purposes only on this specific view
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response
