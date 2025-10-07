from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import CrushProfile, ProfileSubmission, CoachSession, EventRegistration


class CrushSignupForm(UserCreationForm):
    """User registration form for Crush.lu"""
    first_name = forms.CharField(max_length=30, required=True)
    last_name = forms.CharField(max_length=30, required=True)
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class CrushProfileForm(forms.ModelForm):
    """Profile creation/editing form"""

    # Override phone_number to make it required
    phone_number = forms.CharField(
        required=True,
        max_length=20,
        widget=forms.TextInput(attrs={
            'placeholder': '+352 XX XX XX XX',
            'class': 'form-control form-control-lg'
        }),
        help_text='Required for coach screening and event coordination'
    )

    # Override date_of_birth to ensure correct HTML5 date format
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control'
            },
            format='%Y-%m-%d'
        ),
        input_formats=['%Y-%m-%d']
    )

    # Override bio and interests to make them optional
    bio = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Tell us about yourself... What do you love? What makes you smile? (Optional)',
            'class': 'form-control'
        }),
        help_text='Share what makes you unique! (Optional, max 500 characters)'
    )

    interests = forms.CharField(
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Or select categories below...',
            'class': 'form-control'
        }),
        help_text='Select interest categories below or write your own (Optional)'
    )

    class Meta:
        model = CrushProfile
        fields = [
            'phone_number',  # Move to top - most important
            'date_of_birth',
            'gender',
            'location',
            'bio',
            'interests',
            'looking_for',
            'photo_1',
            'photo_2',
            'photo_3',
            'show_full_name',
            'show_exact_age',
            'blur_photos',
        ]
        # Widgets are defined in field overrides above

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob:
            from django.utils import timezone
            age = timezone.now().date().year - dob.year
            if age < 18:
                raise forms.ValidationError("You must be at least 18 years old to join Crush.lu")
            if age > 99:
                raise forms.ValidationError("Please enter a valid date of birth")
        return dob


class ProfileReviewForm(forms.ModelForm):
    """Form for coaches to review profiles"""

    class Meta:
        model = ProfileSubmission
        fields = ['status', 'coach_notes', 'feedback_to_user']
        widgets = {
            'coach_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Internal notes...'}),
            'feedback_to_user': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Feedback for the user...'}),
        }


class CoachSessionForm(forms.ModelForm):
    """Form for scheduling/documenting coach sessions"""

    class Meta:
        model = CoachSession
        fields = ['session_type', 'scheduled_at', 'notes']
        widgets = {
            'scheduled_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Session notes...'}),
        }


class EventRegistrationForm(forms.ModelForm):
    """Form for event registration"""

    class Meta:
        model = EventRegistration
        fields = ['dietary_restrictions', 'special_requests']
        widgets = {
            'dietary_restrictions': forms.TextInput(attrs={'placeholder': 'e.g., vegetarian, gluten-free'}),
            'special_requests': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Any special requests or questions?'}),
        }
