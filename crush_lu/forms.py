from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from allauth.account.forms import SignupForm
from .models import CrushProfile, CrushCoach, ProfileSubmission, CoachSession, EventRegistration, JourneyGift, EventInvitation, CallAttempt
from PIL import Image
import os

# Tailwind CSS classes for form inputs (replacing Bootstrap form-control)
TAILWIND_INPUT = (
    'w-full px-4 py-2.5 border border-gray-300 rounded-lg bg-white text-gray-900 '
    'placeholder:text-gray-400 focus:ring-2 focus:ring-purple-500 focus:border-purple-500 '
    'transition-colors'
)
TAILWIND_INPUT_LG = (
    'w-full px-4 py-3 text-lg border border-gray-300 rounded-lg bg-white text-gray-900 '
    'placeholder:text-gray-400 focus:ring-2 focus:ring-purple-500 focus:border-purple-500 '
    'transition-colors'
)
TAILWIND_SELECT = (
    'w-full px-4 py-2.5 border border-gray-300 rounded-lg bg-white text-gray-900 '
    'focus:ring-2 focus:ring-purple-500 focus:border-purple-500 transition-colors'
)
TAILWIND_TEXTAREA = (
    'w-full px-4 py-3 border border-gray-300 rounded-lg bg-white text-gray-900 '
    'placeholder:text-gray-400 focus:ring-2 focus:ring-purple-500 focus:border-purple-500 '
    'transition-colors resize-y'
)


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
            'class': TAILWIND_INPUT
        })
    )
    last_name = forms.CharField(
        max_length=30,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': _('Last Name'),
            'class': TAILWIND_INPUT
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
            'class': TAILWIND_INPUT_LG
        }),
        help_text=_('Required for coach screening and event coordination')
    )

    # Override date_of_birth to ensure correct HTML5 date format
    date_of_birth = forms.DateField(
        required=True,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': TAILWIND_INPUT
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
            'class': TAILWIND_TEXTAREA
        }),
        help_text=_('Share what makes you unique! (Optional, max 500 characters)')
    )

    interests = forms.CharField(
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': _('Or select categories below...'),
            'class': TAILWIND_TEXTAREA
        }),
        help_text=_('Select interest categories below or write your own (Optional)')
    )

    # Override gender to make it required
    gender = forms.ChoiceField(
        required=True,
        choices=CrushProfile.GENDER_CHOICES,
        widget=forms.Select(attrs={'class': TAILWIND_SELECT}),
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
        widget=forms.Select(attrs={'class': TAILWIND_SELECT}),
        help_text=_('Required')
    )

    # Event languages (multi-select checkbox field for languages spoken at events)
    event_languages = forms.MultipleChoiceField(
        choices=CrushProfile.EVENT_LANGUAGE_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'event-language-checkbox'}),
        required=False,
        label=_('Languages for Events'),
        help_text=_('Which languages can you speak at in-person events?')
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
            'event_languages',
        ]
        # Widgets are defined in field overrides above

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set initial value for event_languages from JSONField
        if self.instance and self.instance.pk and self.instance.event_languages:
            self.initial['event_languages'] = self.instance.event_languages

    def clean_event_languages(self):
        """Ensure event_languages is stored as a list for JSON serialization"""
        languages = self.cleaned_data.get('event_languages', [])
        # Validate that all selected languages are valid choices
        valid_codes = [code for code, _ in CrushProfile.EVENT_LANGUAGE_CHOICES]
        for lang in languages:
            if lang not in valid_codes:
                raise forms.ValidationError(
                    _("Invalid language selection: %(lang)s"),
                    params={'lang': lang}
                )
        return list(languages)

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
        # This validation applies to BOTH new and existing profiles
        phone_verified = False

        if self.instance and self.instance.pk:
            # Existing profile: check phone_verified on the instance
            # Refresh from DB to get latest verification status
            from .models import CrushProfile
            try:
                db_profile = CrushProfile.objects.get(pk=self.instance.pk)
                phone_verified = db_profile.phone_verified
            except CrushProfile.DoesNotExist:
                phone_verified = False
        else:
            # New profile: check if profile was created during phone verification
            # The verify_phone_page and mark_phone_verified views create profiles
            # with phone_verified=True before the form is submitted
            if hasattr(self, 'instance') and hasattr(self.instance, 'user') and self.instance.user:
                from .models import CrushProfile
                try:
                    db_profile = CrushProfile.objects.get(user=self.instance.user)
                    phone_verified = db_profile.phone_verified
                    # Update instance to use the existing profile
                    self.instance = db_profile
                except CrushProfile.DoesNotExist:
                    phone_verified = False

        if not phone_verified:
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

        # Skip validation for existing files (only validate new uploads)
        # This prevents errors when the storage backend can't find existing files
        # (e.g., staging pointing to different container than production)
        from django.core.files.uploadedfile import InMemoryUploadedFile, TemporaryUploadedFile
        if not isinstance(photo, (InMemoryUploadedFile, TemporaryUploadedFile)):
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
            'status': forms.Select(attrs={
                'class': 'w-full px-4 py-2.5 border border-gray-300 rounded-lg bg-white text-gray-900 '
                         'focus:ring-2 focus:ring-purple-500 focus:border-purple-500 transition-colors'
            }),
            'coach_notes': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': _('e.g., Verified identity via video call. Photos match. Genuine interest in dating.'),
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg text-gray-900 '
                         'focus:ring-2 focus:ring-purple-500 focus:border-purple-500 transition-colors '
                         'placeholder:text-gray-400'
            }),
            'feedback_to_user': forms.Textarea(attrs={
                'rows': 4,
                'placeholder': _('e.g., Welcome to Crush.lu! Your profile looks great...'),
                'class': 'w-full px-4 py-3 border border-gray-300 rounded-lg text-gray-900 '
                         'focus:ring-2 focus:ring-purple-500 focus:border-purple-500 transition-colors '
                         'placeholder:text-gray-400'
            }),
        }


class CallAttemptForm(forms.ModelForm):
    """Form for logging failed call attempts"""

    class Meta:
        model = CallAttempt
        fields = ['failure_reason', 'notes']
        widgets = {
            'failure_reason': forms.Select(attrs={
                'class': TAILWIND_SELECT,
                'required': True,
            }),
            'notes': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': _('e.g., Left voicemail asking to call back...'),
                'class': TAILWIND_TEXTAREA
            }),
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
    """Dynamic registration form based on event configuration"""

    class Meta:
        model = EventRegistration
        fields = [
            "accessibility_needs",
            "dietary_restrictions",
            "bringing_guest",
            "guest_name",
            "special_requests",
        ]
        widgets = {
            "accessibility_needs": forms.Textarea(
                attrs={
                    "class": TAILWIND_TEXTAREA,
                    "rows": 3,
                    "placeholder": _(
                        "e.g., wheelchair access, hearing assistance, etc."
                    ),
                }
            ),
            "dietary_restrictions": forms.TextInput(
                attrs={
                    "class": TAILWIND_INPUT,
                    "placeholder": _(
                        "e.g., vegetarian, vegan, gluten-free, allergies"
                    ),
                }
            ),
            "bringing_guest": forms.CheckboxInput(
                attrs={
                    "class": "h-4 w-4 rounded border-gray-300 text-crush-purple focus:ring-crush-purple"
                }
            ),
            "guest_name": forms.TextInput(
                attrs={
                    "class": TAILWIND_INPUT,
                    "placeholder": _("Guest's full name"),
                }
            ),
            "special_requests": forms.Textarea(
                attrs={
                    "class": TAILWIND_TEXTAREA,
                    "rows": 4,
                    "placeholder": _("Any other requests or questions?"),
                }
            ),
        }

    def __init__(self, *args, event=None, **kwargs):
        """Initialize form with event context"""
        super().__init__(*args, **kwargs)

        # Remove fields not applicable to this event
        if event:
            if not event.has_food_component:
                self.fields.pop("dietary_restrictions", None)

            if not event.allow_plus_ones:
                self.fields.pop("bringing_guest", None)
                self.fields.pop("guest_name", None)


class CrushCoachForm(forms.ModelForm):
    """Form for coaches to edit their coach profile"""

    bio = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': _('Share your coaching philosophy and approach...'),
            'class': TAILWIND_TEXTAREA
        }),
        help_text=_('Tell users about your coaching style and experience (max 500 characters)')
    )

    specializations = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            'placeholder': _('e.g., Young professionals, Students, 35+, LGBTQ+'),
            'class': TAILWIND_INPUT
        }),
        help_text=_('What groups or demographics do you specialize in coaching?')
    )

    photo = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': TAILWIND_INPUT,
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
            'class': TAILWIND_INPUT_LG,
            'placeholder': _('Enter new password'),
            'autocomplete': 'new-password',
        }),
        help_text=_('Your password must be at least 8 characters.')
    )
    password2 = forms.CharField(
        label=_('Confirm Password'),
        widget=forms.PasswordInput(attrs={
            'class': TAILWIND_INPUT_LG,
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

    # Media Upload Fields
    chapter1_image = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Photo Puzzle Image'),
        help_text=_('This image will be revealed as a puzzle in Chapter 1. Recommended: 800x800px square image.')
    )

    chapter3_image_1 = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Slideshow Photo 1'),
        help_text=_('First photo for the Chapter 3 slideshow.')
    )

    chapter3_image_2 = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Slideshow Photo 2'),
    )

    chapter3_image_3 = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Slideshow Photo 3'),
    )

    chapter3_image_4 = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Slideshow Photo 4'),
    )

    chapter3_image_5 = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'image/*',
            'class': 'gift-file-input'
        }),
        label=_('Slideshow Photo 5'),
    )

    chapter4_video = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'video/*',
            'class': 'gift-file-input'
        }),
        label=_('Video Message'),
        help_text=_('Record a video message for Chapter 4. Formats: MP4, MOV (max 50MB).')
    )

    chapter5_letter_music = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={
            'accept': 'audio/*',
            'class': 'gift-file-input'
        }),
        label=_('Letter Music'),
        help_text=_('Add background music for the Future Letter (Chapter 5). Formats: MP3, WAV, M4A (max 10MB).')
    )

    class Meta:
        model = JourneyGift
        fields = [
            'recipient_name', 'date_first_met', 'location_first_met',
            'sender_message', 'recipient_email',
            'chapter1_image',
            'chapter3_image_1', 'chapter3_image_2', 'chapter3_image_3',
            'chapter3_image_4', 'chapter3_image_5',
            'chapter4_video', 'chapter5_letter_music'
        ]

    def clean_date_first_met(self):
        """Validate that the date is not in the future"""
        date_met = self.cleaned_data.get('date_first_met')
        if date_met:
            from django.utils import timezone
            today = timezone.now().date()

            if date_met > today:
                raise forms.ValidationError(_("The date you first met cannot be in the future"))

        return date_met

    def _validate_image_size(self, image, max_size_mb=5):
        """Validate image file size"""
        if image and image.size > max_size_mb * 1024 * 1024:
            raise forms.ValidationError(
                _("Image file size must be less than %(max_size)s MB.") % {'max_size': max_size_mb}
            )
        return image

    def clean_chapter1_image(self):
        return self._validate_image_size(self.cleaned_data.get('chapter1_image'), max_size_mb=5)

    def clean_chapter3_image_1(self):
        return self._validate_image_size(self.cleaned_data.get('chapter3_image_1'), max_size_mb=5)

    def clean_chapter3_image_2(self):
        return self._validate_image_size(self.cleaned_data.get('chapter3_image_2'), max_size_mb=5)

    def clean_chapter3_image_3(self):
        return self._validate_image_size(self.cleaned_data.get('chapter3_image_3'), max_size_mb=5)

    def clean_chapter3_image_4(self):
        return self._validate_image_size(self.cleaned_data.get('chapter3_image_4'), max_size_mb=5)

    def clean_chapter3_image_5(self):
        return self._validate_image_size(self.cleaned_data.get('chapter3_image_5'), max_size_mb=5)

    def clean_chapter5_letter_music(self):
        audio = self.cleaned_data.get('chapter5_letter_music')
        if audio:
            # Validate file size (10MB max)
            if audio.size > 10 * 1024 * 1024:
                raise forms.ValidationError(_("Audio file size must be less than 10 MB."))

            # Validate file type by extension and content type
            allowed_extensions = ['.mp3', '.wav', '.m4a', '.aac']
            allowed_content_types = [
                'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav',
                'audio/mp4', 'audio/x-m4a', 'audio/aac'
            ]

            ext = '.' + audio.name.split('.')[-1].lower() if '.' in audio.name else ''
            content_type = getattr(audio, 'content_type', '')

            if ext not in allowed_extensions and content_type not in allowed_content_types:
                raise forms.ValidationError(
                    _("Invalid audio format. Please use MP3, WAV, or M4A files.")
                )
        return audio

    def clean_chapter4_video(self):
        video = self.cleaned_data.get('chapter4_video')
        if video:
            # Validate file size (50MB max)
            if video.size > 50 * 1024 * 1024:
                raise forms.ValidationError(_("Video file size must be less than 50 MB."))

            # Validate file type by extension and content type
            allowed_extensions = ['.mp4', '.mov', '.m4v']
            allowed_content_types = ['video/mp4', 'video/quicktime', 'video/x-m4v']

            ext = '.' + video.name.split('.')[-1].lower() if '.' in video.name else ''
            content_type = getattr(video, 'content_type', '')

            if ext not in allowed_extensions and content_type not in allowed_content_types:
                raise forms.ValidationError(
                    _("Invalid video format. Please use MP4 or MOV files.")
                )
        return video


class InvitationAcceptanceForm(forms.Form):
    """
    Form for accepting event invitations with age verification.

    Security Requirements:
    - Captures actual date of birth (no hardcoded ages)
    - Validates 18+ age requirement
    - Used for external guest invitation acceptance
    """
    date_of_birth = forms.DateField(
        required=True,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': TAILWIND_INPUT,
                'placeholder': 'YYYY-MM-DD',
            },
            format='%Y-%m-%d'
        ),
        input_formats=['%Y-%m-%d'],
        label=_('Date of Birth'),
        help_text=_('You must be 18+ to join Crush.lu')
    )

    agree_to_terms = forms.BooleanField(
        required=True,
        label=_('I agree to the Terms of Service and Privacy Policy'),
        widget=forms.CheckboxInput(attrs={
            'class': 'w-4 h-4 text-purple-600 bg-gray-100 border-gray-300 rounded focus:ring-purple-500'
        })
    )

    def __init__(self, *args, invitation=None, **kwargs):
        """
        Initialize form with invitation context.

        Args:
            invitation: EventInvitation instance for validation
        """
        super().__init__(*args, **kwargs)
        self.invitation = invitation

    def clean_date_of_birth(self):
        """
        Validate date of birth with 18+ age requirement.

        This validation ensures:
        - No future dates
        - User is at least 18 years old
        - Valid date range (not more than 120 years old)

        Raises:
            ValidationError: If age requirements are not met
        """
        from django.utils import timezone

        dob = self.cleaned_data.get('date_of_birth')
        if not dob:
            raise ValidationError(_("Date of birth is required"))

        today = timezone.now().date()

        # Prevent future dates
        if dob > today:
            raise ValidationError(_("Date of birth cannot be in the future"))

        # Calculate age correctly by checking if birthday has occurred this year
        # Use <= for proper boundary checking (birthday this year counts as full age)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        # Sanity check: maximum age 120 (check first to give better error message)
        if age > 120:
            raise ValidationError(_("Please enter a valid date of birth"))

        # Enforce 18+ requirement
        if age < 18:
            raise ValidationError(_("You must be at least 18 years old to join Crush.lu"))

        return dob

    def clean_agree_to_terms(self):
        """Validate that user agreed to terms."""
        agreed = self.cleaned_data.get('agree_to_terms')
        if not agreed:
            raise ValidationError(_("You must agree to the Terms of Service and Privacy Policy"))
        return agreed
