# Testing Checklists for GitHub Issues

Copy-paste these into the respective GitHub issues.

---

## Issue #134 - Screening Call Event Languages

### Description
During the screening call, coaches need to ask users which languages they can speak at in-person events. This is separate from `preferred_language` (UI/email preference).

**Languages supported:** English (en), German (de), French (fr), Luxembourgish (lu)

### Implementation Status: DONE

**Files Modified:**
| File | Change |
|------|--------|
| `crush_lu/models/profiles.py` | Added `EVENT_LANGUAGE_CHOICES` and `event_languages` JSONField |
| `crush_lu/forms.py` | Added MultipleChoiceField with CheckboxSelectMultiple widget |
| `crush_lu/templates/crush_lu/partials/edit_profile_form.html` | Added checkbox UI for user editing |
| `crush_lu/templates/crush_lu/create_profile.html` | Added checkbox UI for profile creation |
| `crush_lu/templates/crush_lu/_screening_call_section.html` | Display event languages for coach screening |
| `crush_lu/templates/crush_lu/coach_edit_profile.html` | Added form field for coach editing |
| `crush_lu/templates/crush_lu/coach_review_profile.html` | Display in profile info (read-only) |

### Testing Checklist

#### 1. Database Migration
- [ ] Migration `0047_add_event_languages` exists
- [ ] Migration applies without errors: `python manage.py migrate crush_lu`
- [ ] Field `event_languages` exists in `crush_lu_crushprofile` table

#### 2. User Flow - Profile Creation
- [ ] Navigate to `/create-profile/`
- [ ] See "Languages for Events" section with 4 checkboxes:
  - [ ] English
  - [ ] Deutsch
  - [ ] Francais
  - [ ] Letzebuergesch
- [ ] Select multiple languages (e.g., EN + LU)
- [ ] Submit form successfully
- [ ] Verify in DB: `event_languages = ["en", "lu"]`

#### 3. User Flow - Profile Edit
- [ ] Navigate to `/profile/edit/`
- [ ] See current selections pre-checked
- [ ] Modify selections (add/remove languages)
- [ ] Save changes
- [ ] Verify changes persisted in DB

#### 4. Coach Flow - Screening Call Section
- [ ] Login as coach (e.g., `coach.marie` / `crushcoach2025`)
- [ ] Navigate to `/coach/dashboard/`
- [ ] Click on a pending profile to review
- [ ] In screening call section, see "Event Languages:" with badges
- [ ] Verify flags display correctly (flag emojis + language names)
- [ ] If no languages set, see "Not set" in italic

#### 5. Coach Flow - Review Profile (Read-only)
- [ ] On coach review page, see event languages in profile info section
- [ ] Languages displayed as blue pill badges

#### 6. Coach Flow - Edit Profile
- [ ] Navigate to coach edit profile page
- [ ] See event languages checkboxes
- [ ] Modify and save
- [ ] Verify changes saved

#### 7. Edge Cases
- [ ] Submit profile with NO event languages selected -> saves as empty array `[]`
- [ ] Display shows "Not set" when empty
- [ ] Form validation allows empty selection (not required)

---

## Issue #114 - Review E-mail Sending for the Gift

### Description
Email for gift notifications is not sent in the language that was chosen by the user.

### Investigation Needed
- Check `crush_lu/email_helpers.py` for gift email functions
- Verify `preferred_language` is being used when sending gift emails
- Check gift email templates have translations

### Testing Checklist

#### 1. Identify the Problem
- [ ] Find the gift email sending code
- [ ] Check if `preferred_language` is passed to email context
- [ ] Verify email templates exist in all 3 languages (en, de, fr)

#### 2. Test Gift Email - German User
- [ ] Create/use a user with `preferred_language = 'de'`
- [ ] Send a gift to this user
- [ ] Check email received is in German
- [ ] Verify subject line is in German
- [ ] Verify email body content is in German

#### 3. Test Gift Email - French User
- [ ] Create/use a user with `preferred_language = 'fr'`
- [ ] Send a gift to this user
- [ ] Check email received is in French
- [ ] Verify subject line is in French
- [ ] Verify email body content is in French

#### 4. Test Gift Email - English User (Default)
- [ ] Create/use a user with `preferred_language = 'en'`
- [ ] Send a gift to this user
- [ ] Check email received is in English

#### 5. Edge Cases
- [ ] User with no `preferred_language` set -> defaults to English
- [ ] All dynamic content (names, dates) displays correctly

---

## Issue #111 - Translation has {month} for dynamic value

### Description
In translations, there's `{month}` placeholder that's not being properly replaced with the actual month value.

### Investigation Needed
- Search for `{month}` in translation files (`.po` files)
- Find where this string is used and how it's formatted
- Check if using correct Django translation syntax

### Testing Checklist

#### 1. Find the Issue
- [ ] Search `.po` files for `{month}` placeholder
- [ ] Identify which template/view uses this string
- [ ] Check Python code for proper `.format()` or `%` substitution

#### 2. Fix Verification
- [ ] If using `{% blocktrans %}`, ensure variable is passed correctly
- [ ] Example: `{% blocktrans with month=month_name %}In {{ month }}{% endblocktrans %}`
- [ ] Run `python manage.py compilemessages` after fixing

#### 3. Test in Each Language
- [ ] View the page/feature in English - month displays correctly
- [ ] View the page/feature in German - month displays correctly
- [ ] View the page/feature in French - month displays correctly

---

## Issue #108 - Word Scramble Challenge Shuffle Bug

### Description
The shuffle button in Word Scramble challenge is buggy.

### Current Implementation (word_scramble.html)
```javascript
// Shuffle function - shuffles words, not individual letters
shuffleBtn.addEventListener('click', function() {
    const currentOrder = scrambledWords.join(' ');
    let attempts = 0;
    let newOrder;

    // Keep shuffling until we get a different arrangement (or max 50 attempts)
    do {
        newOrder = [...scrambledWords];
        // Fisher-Yates shuffle for words
        for (let i = newOrder.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [newOrder[i], newOrder[j]] = [newOrder[j], newOrder[i]];
        }
        attempts++;
    } while (newOrder.join(' ') === currentOrder && attempts < 50);

    scrambledWords = newOrder;
    scrambledDisplay.textContent = scrambledWords.join('  .  ');
});
```

### Testing Checklist

#### 1. Basic Shuffle Functionality
- [ ] Navigate to a Word Scramble challenge
- [ ] Click "Shuffle" button
- [ ] Words rearrange in display
- [ ] Animation works (scale effect)

#### 2. Shuffle Edge Cases
- [ ] Single word scramble - does shuffle still work?
- [ ] Two words - does it actually change order?
- [ ] Many words (5+) - shuffles correctly?
- [ ] Click shuffle multiple times rapidly - no errors?

#### 3. Visual Issues
- [ ] Words separated by bullet (.) after shuffle
- [ ] No letters get lost during shuffle
- [ ] Display stays within container bounds

#### 4. Specific Bug Investigation
- [ ] What exactly is "buggy"? Document the specific issue:
  - [ ] Words don't change order?
  - [ ] Letters get scrambled instead of words?
  - [ ] Display glitches?
  - [ ] JavaScript errors in console?

#### 5. Answer Validation After Shuffle
- [ ] After shuffling, correct answer still works
- [ ] Shuffling doesn't affect the expected answer

---

## Issue #107 - Button "Return to Connection" Layout/Design Issue

### Description
The "Retour a la connexion" (Back to connection) button doesn't display correctly.

### Code Referenced
```html
<a href="/fr/journey/" rel="nofollow noreferrer">
    Retour a la connexion
</a>
```

### Testing Checklist

#### 1. Find the Button Location
- [ ] Identify which template contains this button
- [ ] Check CSS classes applied to the button
- [ ] Compare with other similar buttons that work

#### 2. Visual Issues to Check
- [ ] Button has proper styling (background, border, padding)
- [ ] Button is visible (not hidden or transparent)
- [ ] Text is readable (proper color contrast)
- [ ] Button is properly sized
- [ ] Button is aligned correctly

#### 3. Test in All Languages
- [ ] English: "Back to Journey" - displays correctly
- [ ] German: displays correctly
- [ ] French: "Retour a la connexion" - displays correctly

#### 4. Responsive Design
- [ ] Button looks correct on desktop (1920px)
- [ ] Button looks correct on tablet (768px)
- [ ] Button looks correct on mobile (375px)

#### 5. Fix Verification
- [ ] Add proper CSS class (e.g., `nav-btn` or `submit-btn`)
- [ ] Ensure icon displays if expected
- [ ] Button is clickable and navigates correctly

---

## Issue #106 - Chapter 3 Multiple Choice Feedback

### Description
Multiple Choice challenge doesn't inform the user that a correct answer is needed.

### Current Behavior (multiple_choice.html)
- Wrong answer shows: "Not quite right! Try a different answer" with shake animation
- Message disappears after 3 seconds
- User must keep guessing

### Testing Checklist

#### 1. Current UX Flow
- [ ] Go to Chapter 3 Multiple Choice question
- [ ] Select wrong answer
- [ ] See error message appears
- [ ] Message disappears after 3 seconds
- [ ] Can retry with different answer

#### 2. Issue Clarification
- [ ] Is the problem that users don't know they MUST answer correctly?
- [ ] Or that there's no indication which answer was wrong?
- [ ] Or that the feedback message is too brief?

#### 3. Suggested Improvements to Test
- [ ] Add text: "You must select the correct answer to continue"
- [ ] Keep error message visible longer (or until user selects new option)
- [ ] Show hint after X wrong attempts
- [ ] Disable wrong option after selecting it

#### 4. Fix Verification
- [ ] Error message clearly indicates correct answer is required
- [ ] User understands they need to try again
- [ ] UX is not frustrating (balance between guidance and challenge)

---

## Issue #105 - Review Wonderland of You

### Description
General review of the Wonderland journey feature.

### Testing Checklist

#### 1. Journey Creation (Management Command)
- [ ] Run: `python manage.py create_wonderland_journey --first-name "Test" --last-name "User"`
- [ ] Journey created successfully
- [ ] All 6 chapters created
- [ ] Challenges created for each chapter
- [ ] Rewards created for applicable chapters

#### 2. Journey Access
- [ ] User with matching name can access journey
- [ ] User sees personalized welcome message
- [ ] Journey map displays all chapters
- [ ] Locked chapters show lock icon

#### 3. Chapter 1 - First Meeting
- [ ] Chapter unlocks correctly
- [ ] Challenge(s) work properly
- [ ] Reward (Photo Puzzle) unlocks after completion
- [ ] Photo puzzle displays correctly (if image uploaded)

#### 4. Chapter 2 - First Impressions
- [ ] Unlocks after Chapter 1 complete
- [ ] "Would You Rather" or Multiple Choice works
- [ ] Personal reflection questions work

#### 5. Chapter 3 - Growing Connection
- [ ] Multiple choice questions work (Issue #106)
- [ ] Photo slideshow reward works

#### 6. Chapter 4 - Adventures Together
- [ ] Timeline sort challenge works
- [ ] Audio reward plays (if uploaded)

#### 7. Chapter 5 - Looking Forward
- [ ] Word scramble works (Issue #108)
- [ ] Future letter reward displays

#### 8. Chapter 6 - Your Story Continues
- [ ] Final reflection works
- [ ] Certificate generates
- [ ] Final response captures user choice

#### 9. Media Upload (NEW FEATURE)
- [ ] Run command with `--chapter1-image path/to/image.jpg`
- [ ] Image uploads to `users/{user_id}/journey_rewards/photos/`
- [ ] Image displays in photo puzzle reward

#### 10. Multi-Language Support
- [ ] Journey displays in user's preferred language
- [ ] All challenges translate correctly
- [ ] All rewards translate correctly

---

## Issue #46 - Implement assetlinks.json for Android PWA

### Description
Implement Digital Asset Links for Android App Links verification.

### Implementation Requirements
- File must be at: `https://crush.lu/.well-known/assetlinks.json`
- Content-Type: `application/json`
- Must be publicly accessible (no auth)
- No redirects

### Testing Checklist

#### 1. File Implementation
- [ ] Create view or static file for `/.well-known/assetlinks.json`
- [ ] Returns correct JSON structure:
```json
[{
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
        "namespace": "web",
        "site": "https://crush.lu"
    }
}]
```
- [ ] Content-Type header is `application/json`

#### 2. URL Configuration
- [ ] Add URL pattern outside `i18n_patterns`
- [ ] No authentication required
- [ ] No language prefix (not `/en/.well-known/`)

#### 3. Local Testing
- [ ] Access `http://localhost:8000/.well-known/assetlinks.json`
- [ ] Returns valid JSON
- [ ] No redirects occur

#### 4. Production Testing
- [ ] Deploy to production
- [ ] Access `https://crush.lu/.well-known/assetlinks.json`
- [ ] Returns valid JSON
- [ ] Use Google's verification tool: https://developers.google.com/digital-asset-links/tools/generator

#### 5. PWA Benefits Verification
- [ ] Install PWA on Android device
- [ ] Open crush.lu link - should open in PWA directly
- [ ] No "Open with" dialog appears
- [ ] OAuth flow works smoothly in PWA

---

# Quick Reference: Test Credentials

## Coach Accounts
- `coach.marie` / `marie@crush.lu` / `crushcoach2025`
- `coach.thomas` / `thomas@crush.lu` / `crushcoach2025`
- `coach.sophie` / `sophie@crush.lu` / `crushcoach2025`

## URLs
- Coach Dashboard: `/coach/dashboard/`
- Profile Creation: `/create-profile/`
- Profile Edit: `/profile/edit/`
- Journey Map: `/journey/`
- Events: `/events/`

## Management Commands
```bash
# Create test journey
python manage.py create_wonderland_journey --first-name "Alice" --last-name "Test"

# With media files
python manage.py create_wonderland_journey \
    --first-name "Alice" \
    --last-name "Test" \
    --chapter1-image "path/to/puzzle.jpg" \
    --chapter3-image "path/to/slideshow.jpg"

# Create sample events
python manage.py create_sample_events

# Create test profiles
python manage.py create_sample_crush_profiles
```
