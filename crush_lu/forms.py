from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from allauth.account.forms import SignupForm
from .models import CrushProfile, CrushCoach, ProfileSubmission, CoachSession, EventRegistration
from PIL import Image
import os


class CrushSignupForm(SignupForm):
    """
    Allauth-compatible signup form for Crush.lu
    Extends allauth.account.forms.SignupForm for proper integration with social login
    """
    first_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'First Name',
            'class': 'form-control'
        })
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Last Name',
            'class': 'form-control'
        })
    )

    def clean_email(self):
        """
        Check if email already exists
        This provides better UX than letting it fail during save()
        """
        email = self.cleaned_data.get('email')

        if email:
            # Normalize: strip whitespace and lowercase
            email = email.strip().lower()

        # Check if a user with this email already exists
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(
                'An account with this email already exists. '
                'Please login or use a different email address.'
            )

        return email

    def save(self, request):
        """
        Allauth will handle user creation and EmailAddress creation automatically
        This includes social login users (LinkedIn, Google, etc.)
        """
        # Let Allauth handle the user creation
        # It will raise ValidationError if email already exists
        user = super(CrushSignupForm, self).save(request)

        # Update additional fields
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']

        # Note: Don't set username = email after user creation
        # Allauth already handles this via ACCOUNT_USER_MODEL_USERNAME_FIELD setting
        # Setting it here can cause duplicate username errors

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
        required=True,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control'
            },
            format='%Y-%m-%d'
        ),
        input_formats=['%Y-%m-%d'],
        help_text='Must be 18+ to join'
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

    # Override gender to make it required
    gender = forms.ChoiceField(
        required=True,
        choices=CrushProfile.GENDER_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Required'
    )

    # Override location to make it required
    location = forms.CharField(
        required=True,
        max_length=100,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Your city in Luxembourg'
    )

    # Override looking_for to make it required
    looking_for = forms.ChoiceField(
        required=True,
        choices=CrushProfile.LOOKING_FOR_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text='Required'
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

    def clean_photo_1(self):
        """Validate photo_1: file size, type, and image content"""
        return self._validate_photo(self.cleaned_data.get('photo_1'), 'Photo 1')

    def clean_photo_2(self):
        """Validate photo_2: file size, type, and image content"""
        return self._validate_photo(self.cleaned_data.get('photo_2'), 'Photo 2')

    def clean_photo_3(self):
        """Validate photo_3: file size, type, and image content"""
        return self._validate_photo(self.cleaned_data.get('photo_3'), 'Photo 3')

    def _validate_photo(self, photo, field_name):
        """
        Validate uploaded photo file

        Security checks:
        - File size limit: 10MB
        - File type: JPEG, PNG, WebP only
        - Image dimensions: Max 4000x4000px
        - Content validation: Verify it's actually an image
        """
        if not photo:
            return photo

        # Check file size (10MB limit)
        max_size = 10 * 1024 * 1024  # 10MB in bytes
        if photo.size > max_size:
            raise ValidationError(
                f"{field_name} must be less than 10MB. Your file is {photo.size / (1024*1024):.1f}MB."
            )

        # Check file extension
        allowed_extensions = ['.jpg', '.jpeg', '.png', '.webp']
        ext = os.path.splitext(photo.name)[1].lower()
        if ext not in allowed_extensions:
            raise ValidationError(
                f"{field_name} must be a JPEG, PNG, or WebP image. You uploaded: {ext}"
            )

        # Verify it's actually an image (content validation)
        try:
            img = Image.open(photo)
            img.verify()  # Verify it's a valid image

            # Reset file pointer after verify()
            photo.seek(0)

            # Re-open to check dimensions (verify() closes the file)
            img = Image.open(photo)
            width, height = img.size

            # Check dimensions (max 4000x4000)
            max_dimension = 4000
            if width > max_dimension or height > max_dimension:
                raise ValidationError(
                    f"{field_name} dimensions too large ({width}x{height}px). "
                    f"Maximum: {max_dimension}x{max_dimension}px."
                )

            # Check minimum dimensions (at least 200x200)
            min_dimension = 200
            if width < min_dimension or height < min_dimension:
                raise ValidationError(
                    f"{field_name} is too small ({width}x{height}px). "
                    f"Minimum: {min_dimension}x{min_dimension}px for clear photos."
                )

            # Reset file pointer for saving
            photo.seek(0)

        except Exception as e:
            raise ValidationError(
                f"{field_name} is not a valid image file. Error: {str(e)}"
            )

        return photo


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


class CrushCoachForm(forms.ModelForm):
    """Form for coaches to edit their coach profile"""

    bio = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Share your coaching philosophy and approach...',
            'class': 'form-control'
        }),
        help_text='Tell users about your coaching style and experience (max 500 characters)'
    )

    specializations = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g., Young professionals, Students, 35+, LGBTQ+',
            'class': 'form-control'
        }),
        help_text='What groups or demographics do you specialize in coaching?'
    )

    photo = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*'
        }),
        help_text='Upload a professional photo that users will see'
    )

    class Meta:
        model = CrushCoach
        fields = ['bio', 'specializations', 'photo']


class CrushSetPasswordForm(forms.Form):
    """
    Form for Facebook-registered users to set a password for email/password login.

    This enables users who signed up with Facebook to also login using
    their email and password, providing a backup login method.
    """
    password1 = forms.CharField(
        label='New Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter new password',
            'autocomplete': 'new-password',
        }),
        help_text='Your password must be at least 8 characters.'
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Confirm new password',
            'autocomplete': 'new-password',
        })
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password1(self):
        """Validate password strength using Django's password validators."""
        password1 = self.cleaned_data.get('password1')
        try:
            validate_password(password1, self.user)
        except ValidationError as e:
            raise forms.ValidationError(list(e.messages))
        return password1

    def clean(self):
        """Validate that both passwords match."""
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Passwords do not match.')

        return cleaned_data

    def save(self):
        """Set the user's password."""
        password = self.cleaned_data['password1']
        self.user.set_password(password)
        self.user.save()
        return self.user
