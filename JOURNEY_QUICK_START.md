# 🚀 Journey System - Quick Start Guide

## Create Your First Journey in 30 Seconds

### Option 1: One-Command Setup (Recommended)

```bash
python manage.py create_wonderland_journey \
    --first-name Marie \
    --last-name Dupont \
    --date-met 2024-10-15 \
    --location-met "Café de Paris"
```

**This creates:**
- ✅ Special User Experience for Marie Dupont
- ✅ Complete 6-chapter "Wonderland of You" journey
- ✅ All challenges pre-configured
- ✅ All reward placeholders ready
- ✅ Ready to use immediately!

### What Gets Created

**Chapter 1: Down the Rabbit Hole** 🐇
- Riddle about the date you met
- Word scramble challenge
- Photo reveal reward

**Chapter 2: Garden of Rare Flowers** 💐
- 4 multiple choice personality questions
- Memory matching game
- Poem reward

**Chapter 3: Gallery of Moments** 🎭
- Timeline sorting challenge
- "The moment that changed" question
- Photo slideshow reward

**Chapter 4: Carnival of Courage** 🎪
- 3 "Would You Rather" questions
- Open text reflection
- Voice message reward

**Chapter 5: Starlit Observatory** 🌙
- Dream-building questions
- Future vision text input
- Future letter reward

**Chapter 6: Door to Tomorrow** 💝
- 3-part final riddle
- Grand reveal message
- Completion certificate

---

## How to Customize

### Step 1: Run the Command
```bash
python manage.py create_wonderland_journey \
    --first-name [HER NAME] \
    --last-name [HER LAST NAME] \
    --date-met [YYYY-MM-DD] \
    --location-met "[WHERE YOU MET]"
```

### Step 2: Customize in Admin

1. **Go to Admin** → Journey Configurations → "The Wonderland of You"
2. **Edit personal details:**
   - Update `final_message` with your big reveal
   - Set `date_first_met` and `location_first_met`

3. **Customize challenges:**
   - Admin → Journey Challenges → Edit each one
   - Change questions to be more personal
   - Update success messages with specific memories
   - Adjust points and hints

4. **Upload media for rewards:**
   - Admin → Journey Rewards → Edit each reward
   - Upload photos, audio, video files
   - Update messages with your poems/letters

### Step 3: Test It

1. Create a test user account with matching first/last name
2. Log in as that user
3. You'll be auto-redirected to the journey!

---

## Reward Media Uploads

### Photos
- **Chapter 1**: Photo from your first meeting
- **Chapter 3**: 5-10 photos of moments together

### Audio/Video
- **Chapter 4**: Record a voice message about your feelings
- **Chapter 5**: Optional video message

### Where to Upload
Admin → Journey Rewards → Select reward → Upload files

---

## Personalizing Messages

### Key Places to Customize:

1. **Challenge Success Messages**
   - After each correct answer
   - Add specific memories
   - Make it personal!

2. **Chapter Completion Messages**
   - End of each chapter
   - Tie to the theme
   - Build anticipation

3. **Final Message (Chapter 6)**
   - THE BIG REVEAL
   - Your declaration
   - The invitation/question

### Example Personalization:

**Generic:**
> "Yes! You got it right."

**Personal:**
> "Yes! I remember that day at Café de Paris. You ordered a cappuccino and talked about your dreams for 3 hours. That's when I knew you were different."

---

## Testing Checklist

- [ ] Run create command with your data
- [ ] Log into admin
- [ ] Verify all 6 chapters created
- [ ] Customize at least Chapter 1 & 6
- [ ] Upload at least one photo reward
- [ ] Create test user (matching name)
- [ ] Log in as test user
- [ ] Complete Chapter 1
- [ ] Verify unlock of Chapter 2
- [ ] Test hints system
- [ ] Complete full journey
- [ ] View certificate

---

## User Experience

When Marie Dupont logs in:

1. **Auto-detect** → System finds her Special Experience
2. **Auto-redirect** → Taken to journey map
3. **Visual pathway** → See 6 chapters, only Chapter 1 unlocked
4. **Chapter 1** → Read story, solve riddles
5. **Reward** → Photo puzzle reveals
6. **Progress** → Chapter 2 unlocks automatically
7. **Continue** → Through all 6 chapters
8. **Final reveal** → Your message + Yes/Thinking buttons
9. **Certificate** → Beautiful printable completion certificate

---

## Troubleshooting

### Journey not showing?
- Check Special User Experience is `is_active=True`
- Check Journey Configuration is `is_active=True`
- Verify first/last name match exactly
- Check user logged in (not just visiting)

### Challenges not working?
- Verify `correct_answer` is set
- Check `alternative_answers` array format
- Test answer validation in admin

### Rewards not showing?
- Complete all challenges in chapter first
- Check reward is linked to correct chapter
- Verify media files uploaded successfully

---

## Advanced: Manual Creation

If you want to create everything from scratch in admin:

### Step 1: Special User Experience
Admin → Special User Experiences → Add

### Step 2: Journey Configuration
Admin → Journey Configurations → Add
- Link to Special Experience created above

### Step 3: Add Chapters (x6)
Admin → Journey Chapters → Add (repeat 6 times)
- Set `chapter_number`: 1, 2, 3, 4, 5, 6
- Set `background_theme` for visual variety

### Step 4: Add Challenges
Admin → Journey Challenges → Add
- Link to chapter
- Set `challenge_order`: 1, 2, 3...
- Configure questions and answers

### Step 5: Add Rewards
Admin → Journey Rewards → Add
- Link to chapter
- Upload media
- Set messages

**Time required:** ~2-3 hours for full customization

---

## Tips for Maximum Impact

1. **Be Specific**: Generic messages are nice, specific memories are magical
2. **Use Photos**: Visual rewards are powerful emotional triggers
3. **Record Voice**: Your voice reading a poem > text of a poem
4. **Build Anticipation**: Each chapter should hint at what's coming
5. **Nail Chapter 6**: This is your shot - make it count
6. **Test First**: Complete it yourself before sending

---

## Example Timeline

**Monday**: Run command, customize in admin
**Tuesday**: Upload photos/media, personalize messages
**Wednesday**: Test with dummy account
**Thursday**: Polish and finalize
**Friday**: Activate and send invitation!

---

## What Happens After "Yes"?

When they click "Yes, let's see where this goes 💫":

1. Response recorded in database
2. `journey_progress.final_response = 'yes'`
3. You get notified (check Admin → Journey Progress)
4. They see your next-step message
5. Certificate generated with confetti 🎉

Check their response:
```
Admin → Journey Progress Records → [User] → Final Response
```

---

## Questions?

- Check [JOURNEY_SYSTEM_IMPLEMENTATION.md](JOURNEY_SYSTEM_IMPLEMENTATION.md) for technical details
- Review inline admin help text
- Test with dummy data first
- Read code comments in models

---

**Ready to create magic? Run that command and let the journey begin! ✨**
