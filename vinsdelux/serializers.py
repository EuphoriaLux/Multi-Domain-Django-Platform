"""
Django REST Framework serializers for VinsDelux API endpoints
"""

from rest_framework import serializers
from .models import VdlPlot, VdlPlotReservation, VdlProducer, VdlAdoptionPlan, PlotStatus
from django.contrib.auth.models import User


class VdlProducerSerializer(serializers.ModelSerializer):
    """Serializer for wine producers"""
    
    class Meta:
        model = VdlProducer
        fields = [
            'id', 'name', 'slug', 'region', 'vineyard_size', 'elevation',
            'soil_type', 'sun_exposure', 'vineyard_features', 'map_x_position',
            'map_y_position', 'description', 'website', 'is_featured_on_homepage'
        ]


class VdlAdoptionPlanSerializer(serializers.ModelSerializer):
    """Serializer for adoption plans"""
    
    main_image_url = serializers.SerializerMethodField()
    coffret_name = serializers.CharField(source='associated_coffret.name', read_only=True)
    category_name = serializers.CharField(source='category.get_name_display', read_only=True)
    
    class Meta:
        model = VdlAdoptionPlan
        fields = [
            'id', 'name', 'slug', 'short_description', 'full_description',
            'price', 'duration_months', 'coffrets_per_year', 'includes_visit',
            'includes_medallion', 'includes_club_membership', 'visit_details',
            'welcome_kit_description', 'avant_premiere_price', 'main_image_url',
            'coffret_name', 'category_name', 'is_available'
        ]
    
    def get_main_image_url(self, obj):
        """Get the main image URL for the adoption plan"""
        if obj.main_image:
            return obj.main_image.url
        return None


class VdlPlotSerializer(serializers.ModelSerializer):
    """Serializer for vineyard plots"""
    
    producer = VdlProducerSerializer(read_only=True)
    adoption_plans = VdlAdoptionPlanSerializer(many=True, read_only=True)
    display_coordinates = serializers.CharField(read_only=True)
    primary_grape_variety = serializers.CharField(read_only=True)
    latitude = serializers.FloatField(read_only=True)
    longitude = serializers.FloatField(read_only=True)
    is_available = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = VdlPlot
        fields = [
            'id', 'name', 'plot_identifier', 'producer', 'coordinates',
            'latitude', 'longitude', 'plot_size', 'elevation', 'soil_type',
            'sun_exposure', 'microclimate_notes', 'grape_varieties', 'vine_age',
            'harvest_year', 'wine_profile', 'expected_yield', 'status',
            'base_price', 'is_premium', 'is_available', 'display_coordinates',
            'primary_grape_variety', 'adoption_plans', 'created_at', 'updated_at'
        ]


class VdlPlotListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for plot lists"""
    
    producer_name = serializers.CharField(source='producer.name', read_only=True)
    producer_region = serializers.CharField(source='producer.region', read_only=True)
    latitude = serializers.FloatField(read_only=True)
    longitude = serializers.FloatField(read_only=True)
    is_available = serializers.BooleanField(read_only=True)
    primary_grape_variety = serializers.CharField(read_only=True)
    
    class Meta:
        model = VdlPlot
        fields = [
            'id', 'name', 'plot_identifier', 'producer_name', 'producer_region',
            'latitude', 'longitude', 'plot_size', 'elevation', 'soil_type',
            'sun_exposure', 'grape_varieties', 'primary_grape_variety', 'wine_profile',
            'base_price', 'status', 'is_premium', 'is_available'
        ]


class VdlPlotReservationSerializer(serializers.ModelSerializer):
    """Serializer for plot reservations"""
    
    plot_name = serializers.CharField(source='plot.name', read_only=True)
    plot_identifier = serializers.CharField(source='plot.plot_identifier', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = VdlPlotReservation
        fields = [
            'id', 'plot', 'plot_name', 'plot_identifier', 'user', 'user_email',
            'adoption_plan', 'reserved_at', 'expires_at', 'is_confirmed',
            'confirmation_date', 'notes', 'session_data', 'is_expired'
        ]
        read_only_fields = ['reserved_at', 'expires_at', 'user']


class PlotReservationCreateSerializer(serializers.Serializer):
    """Serializer for creating plot reservations"""
    
    plot_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        max_length=10,  # Limit to 10 plots per reservation
        help_text="List of plot IDs to reserve"
    )
    notes = serializers.CharField(
        max_length=1000,
        required=False,
        allow_blank=True,
        help_text="Optional notes for the reservation"
    )
    adoption_plan_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Optional adoption plan ID if already selected"
    )
    
    def validate_plot_ids(self, value):
        """Validate that all plot IDs exist and are available"""
        plots = VdlPlot.objects.filter(id__in=value, status=PlotStatus.AVAILABLE)
        if len(plots) != len(value):
            raise serializers.ValidationError(
                "Some plots are not available or do not exist"
            )
        return value
    
    def validate_adoption_plan_id(self, value):
        """Validate adoption plan exists if provided"""
        if value is not None:
            if not VdlAdoptionPlan.objects.filter(id=value, is_available=True).exists():
                raise serializers.ValidationError(
                    "Adoption plan does not exist or is not available"
                )
        return value


class PlotAvailabilitySerializer(serializers.Serializer):
    """Serializer for plot availability responses"""
    
    available_count = serializers.IntegerField()
    reserved_count = serializers.IntegerField()
    adopted_count = serializers.IntegerField()
    total_count = serializers.IntegerField()
    available_plots = VdlPlotListSerializer(many=True, read_only=True)
    last_updated = serializers.DateTimeField()


class PlotSelectionSerializer(serializers.Serializer):
    """Serializer for plot selection requests"""
    
    selected_plots = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of selected plot IDs"
    )
    user_notes = serializers.CharField(
        max_length=1000,
        required=False,
        allow_blank=True,
        help_text="User notes about the selection"
    )
    
    def validate_selected_plots(self, value):
        """Validate that selected plots exist and are available"""
        plots = VdlPlot.objects.filter(id__in=value, status=PlotStatus.AVAILABLE)
        if len(plots) != len(value):
            unavailable_ids = set(value) - set(plots.values_list('id', flat=True))
            raise serializers.ValidationError(
                f"Plot(s) with ID(s) {list(unavailable_ids)} are not available"
            )
        return value