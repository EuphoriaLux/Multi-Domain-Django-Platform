from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from allauth.account.forms import SignupForm
from .models import CrushProfile, CrushCoach, ProfileSubmission, CoachSession, EventRegistration, JourneyGift
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
            'placeholder': _('First Name'),
            'class': 'form-control'
        })
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': _('Last Name'),
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
                _('An account with this email already exists. '
                  'Please login or use a different email address.')
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
            'type': 'tel',
            'placeholder': '+352 XX XX XX XX',
            'class': 'form-control form-control-lg'
        }),
        help_text=_('Required for coach screening and event coordination')
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
        help_text=_('Must be 18+ to join')
    )

    # Override bio and interests to make them optional
    bio = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': _('Tell us about yourself... What do you love? What makes you smile? (Optional)'),
            'class': 'form-control'
        }),
        help_text=_('Share what makes you unique! (Optional, max 500 characters)')
    )

    interests = forms.CharField(
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': _('Or select categories below...'),
            'class': 'form-control'
        }),
        help_text=_('Select interest categories below or write your own (Optional)')
    )

    # Override gender to make it required
    gender = forms.ChoiceField(
        required=True,
        choices=CrushProfile.GENDER_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text=_('Required')
    )

    # Override location with canton-based choices (for interactive map selection)
    # Used with the canton map component - canton_map_svg.html
    LOCATION_CHOICES = [
        ('', _('Select your region...')),
        # Luxembourg Cantons (12)
        ('canton-capellen', _('Capellen')),
        ('canton-clervaux', _('Clervaux')),
        ('canton-diekirch', _('Diekirch')),
        ('canton-echternach', _('Echternach')),
        ('canton-esch', _('Esch-sur-Alzette')),
        ('canton-grevenmacher', _('Grevenmacher')),
        ('canton-luxembourg', _('Luxembourg')),
        ('canton-mersch', _('Mersch')),
        ('canton-redange', _('Redange')),
        ('canton-remich', _('Remich')),
        ('canton-vianden', _('Vianden')),
        ('canton-wiltz', _('Wiltz')),
        # Border Regions (for cross-border workers)
        ('border-belgium', _('Belgium (Arlon area)')),
        ('border-germany', _('Germany (Trier/Saarland area)')),
        ('border-france', _('France (Thionville/Metz area)')),
    ]

    location = forms.ChoiceField(
        required=True,
        choices=LOCATION_CHOICES,
        widget=forms.HiddenInput(attrs={'id': 'id_location'}),
        help_text=_('Your region in or near Luxembourg')
    )

    # Override looking_for to make it required
    looking_for = forms.ChoiceField(
        required=True,
        choices=CrushProfile.LOOKING_FOR_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text=_('Required')
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
            today = timezone.now().date()

            # Prevent future dates
            if dob > today:
                raise forms.ValidationError(_("Date of birth cannot be in the future"))

            # Calculate age correctly by checking if birthday has occurred this year
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

            if age < 18:
                raise forms.ValidationError(_("You must be at least 18 years old to join Crush.lu"))
            if age > 99:
                raise forms.ValidationError(_("Please enter a valid date of birth"))
        return dob

    def clean_phone_number(self):
        """Validate phone number format for Luxembourg and neighboring countries"""
        import re

        phone = self.cleaned_data.get('phone_number')
        if not phone:
            return phone

        # Remove all whitespace and dashes for validation
        phone_clean = re.sub(r'[\s\-\(\)]', '', phone)

        # Must start with + for international format
        if not phone_clean.startswith('+'):
            raise forms.ValidationError(
                _("Please enter your phone number in international format (e.g., +352 XX XX XX XX)")
            )

        # Valid country codes for Luxembourg and neighboring countries
        # +352 (Luxembourg), +33 (France), +32 (Belgium), +49 (Germany)
        valid_prefixes = ['+352', '+33', '+32', '+49']
        has_valid_prefix = any(phone_clean.startswith(prefix) for prefix in valid_prefixes)

        if not has_valid_prefix:
            raise forms.ValidationError(
                _("Please enter a phone number from Luxembourg (+352), France (+33), "
                  "Belgium (+32), or Germany (+49)")
            )

        # Check minimum and maximum length (including country code)
        # Luxembourg: +352 + 6-9 digits = 10-13 chars
        # France/Belgium/Germany: +33/+32/+49 + 9-10 digits = 12-13 chars
        if len(phone_clean) < 10:
            raise forms.ValidationError(
                _("Phone number is too short. Please include the full number.")
            )
        if len(phone_clean) > 15:
            raise forms.ValidationError(
                _("Phone number is too long. Please check the format.")
            )

        # Ensure only digits after the +
        if not re.match(r'^\+[0-9]+$', phone_clean):
            raise forms.ValidationError(
                _("Phone number can only contain digits after the country code.")
            )

        return phone

    def clean(self):
        """Additional cross-field validation"""
        cleaned_data = super().clean()

        # Server-side phone verification enforcement
        # Check if this is a profile submission (has instance with user)
        if self.instance and self.instance.pk:
            # For existing profiles, check phone verification status
            if not self.instance.phone_verified:
                raise forms.ValidationError(
                    _("You must verify your phone number before submitting your profile. "
                      "Click the 'Verify' button next to your phone number.")
                )

        return cleaned_data

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
        - File type: JPEG, PNG, WebP only (both extension AND content)
        - MIME type validation: Verify actual file content matches extension
        - Image dimensions: Max 5000x5000px
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

        # Verify MIME type matches actual content (prevents disguised malicious files)
        try:
            import magic
            # Read first 2KB for MIME detection
            file_header = photo.read(2048)
            photo.seek(0)  # Reset file pointer

            mime = magic.from_buffer(file_header, mime=True)
            allowed_mimes = ['image/jpeg', 'image/png', 'image/webp']

            if mime not in allowed_mimes:
                raise ValidationError(
                    f"{field_name} content type ({mime}) does not match allowed image types. "
                    f"Please upload a genuine JPEG, PNG, or WebP image."
                )
        except ImportError:
            # python-magic not installed, skip MIME check
            # This allows graceful degradation in development
            pass
        except Exception:
            # Handle magic library errors (e.g., libmagic not found on system)
            # Pillow verify below provides backup validation
            pass

        # Verify it's actually an image (content validation with Pillow)
        try:
            img = Image.open(photo)
            img.verify()  # Verify it's a valid image

            # Reset file pointer after verify()
            photo.seek(0)

            # Re-open to check dimensions (verify() closes the file)
            img = Image.open(photo)
            width, height = img.size

            # Check dimensions (max 5000x5000 - accommodates modern phone cameras)
            max_dimension = 5000
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

        except ValidationError:
            # Re-raise our own ValidationErrors
            raise
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
            'coach_notes': forms.Textarea(attrs={'rows': 4, 'placeholder': _('Internal notes...')}),
            'feedback_to_user': forms.Textarea(attrs={'rows': 4, 'placeholder': _('Feedback for the user...')}),
        }


class CoachSessionForm(forms.ModelForm):
    """Form for scheduling/documenting coach sessions"""

    class Meta:
        model = CoachSession
        fields = ['session_type', 'scheduled_at', 'notes']
        widgets = {
            'scheduled_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'notes': forms.Textarea(attrs={'rows': 5, 'placeholder': _('Session notes...')}),
        }


class EventRegistrationForm(forms.ModelForm):
    """Form for event registration"""

    class Meta:
        model = EventRegistration
        fields = ['dietary_restrictions', 'special_requests']
        widgets = {
            'dietary_restrictions': forms.TextInput(attrs={'placeholder': _('e.g., vegetarian, gluten-free')}),
            'special_requests': forms.Textarea(attrs={'rows': 3, 'placeholder': _('Any special requests or questions?')}),
        }


class CrushCoachForm(forms.ModelForm):
    """Form for coaches to edit their coach profile"""

    bio = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': _('Share your coaching philosophy and approach...'),
            'class': 'form-control'
        }),
        help_text=_('Tell users about your coaching style and experience (max 500 characters)')
    )

    specializations = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            'placeholder': _('e.g., Young professionals, Students, 35+, LGBTQ+'),
            'class': 'form-control'
        }),
        help_text=_('What groups or demographics do you specialize in coaching?')
    )

    photo = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*'
        }),
        help_text=_('Upload a professional photo that users will see')
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
        label=_('New Password'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': _('Enter new password'),
            'autocomplete': 'new-password',
        }),
        help_text=_('Your password must be at least 8 characters.')
    )
    password2 = forms.CharField(
        label=_('Confirm Password'),
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': _('Confirm new password'),
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
            raise forms.ValidationError(_('Passwords do not match.'))

        return cleaned_data

    def save(self):
        """Set the user's password."""
        password = self.cleaned_data['password1']
        self.user.set_password(password)
        self.user.save()
        return self.user


class JourneyGiftForm(forms.ModelForm):
    """
    Form for creating a Journey Gift to share with someone.

    The sender provides personalization details for the Wonderland journey.
    """
    recipient_name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': _('e.g., My Crush, Marie, Sunshine'),
            'class': 'gift-input gift-input-lg'
        }),
        label=_('Name/Nickname for the Journey'),
        help_text=_('This name will be used throughout the journey story. Use any name or nickname you like.')
    )

    date_first_met = forms.DateField(
        required=True,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'gift-input gift-input-lg'
            },
            format='%Y-%m-%d'
        ),
        input_formats=['%Y-%m-%d'],
        label=_('When did you first meet?'),
        help_text=_('This date will be featured in the journey story.')
    )

    location_first_met = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': _('e.g., Cafe de Paris, Luxembourg City'),
            'class': 'gift-input gift-input-lg'
        }),
        label=_('Where did you first meet?'),
        help_text=_('This location will be featured in the journey story.')
    )

    sender_message = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': _('Optional: Add a personal note that will be shown when they scan the QR code...'),
            'class': 'gift-input'
        }),
        label=_('Personal Message (Optional)'),
        help_text=_('This message will be displayed on the gift landing page.')
    )

    recipient_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={
            'placeholder': _('Optional: their@email.com'),
            'class': 'gift-input'
        }),
        label=_('Recipient Email (Optional)'),
        help_text=_('We can send them a notification when you create the gift.')
    )

    class Meta:
        model = JourneyGift
        fields = ['recipient_name', 'date_first_met', 'location_first_met', 'sender_message', 'recipient_email']

    def clean_date_first_met(self):
        """Validate that the date is not in the future"""
        date_met = self.cleaned_data.get('date_first_met')
        if date_met:
            from django.utils import timezone
            today = timezone.now().date()

            if date_met > today:
                raise forms.ValidationError(_("The date you first met cannot be in the future"))

        return date_met
