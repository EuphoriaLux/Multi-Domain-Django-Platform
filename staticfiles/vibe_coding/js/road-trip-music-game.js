// Road Trip Discovery Game with Music Integration
// Enhanced version with musical journey

class RoadTripMusicGame {
    constructor() {
        this.currentPhase = 0;
        this.currentQuestion = 0;
        this.totalScore = 0;
        this.phaseScores = [0, 0, 0, 0, 0];
        this.userAnswers = [];
        this.maxScore = 125; // 25 questions × 5 max points

        // Button debouncing
        this._buttonCooldown = new Set();

        // Music selections tracking - using 0-based indexing to match phases
        this.musicSelections = [];
        this.currentMusicRound = 0; // 0=opening, 1=phase1, 2=phase2, etc.

        // Track selected genres by round to avoid repeats
        this.selectedGenresByRound = {
            phase1: [],
            phase2: [],
            phase3: [],
            phase4: [],
            phase5: []
        };

        // Track played songs within current genre
        this.currentGenreSongs = [];
        this.playedSongsFromCurrentGenre = [];

        // Phase completion tracking
        this.canSwitchGenres = false;
        this.isWaitingForPhaseTransition = false;

        // Phase summary data
        this.phaseSummaries = [];

        // Track music phase summaries separately (opening, phase1, phase2, phase3, phase4)
        this.musicPhaseSummaries = {};
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [],
            score: 0
        };
        this.allSongsPlayedInPhase = false;
        // Manual evaluation unlock removed - 3-song minimum enforced
        this.isShowingPhaseSummary = false;

        // Background music system
        this.currentAudio = null;
        this.musicQueue = [];
        this.isPlayingBackground = false;
        this.isMusicActive = false; // Track if music should be playing regardless of UI state
        this.selectedGenreData = null;
        this.currentSongTimer = null;
        this.countdownInterval = null;
        this.timeRemaining = 0;
        this.songDuration = 30; // Default song duration in seconds
        this.isManualSelection = true; // Track if current selection is manual vs automatic

        // Test mode settings
        this.testMode = false;
        this.testResults = [];

        // Music library with YouTube links
        this.musicLibrary = {
            // Opening music selection
            opening: {
                title: "Setting the Mood",
                subtitle: "What vibe starts our journey?",
                description: "Every journey needs a soundtrack. What gets you in the mood for adventure?",
                options: [
                    {
                        genre: "Nostalgic 2000s Hits",
                        description: "Taking us back to when we were 16",
                        songs: [
                            { title: "Mr. Brightside - The Killers", youtube: "gGdGFtwCNBE", year: "2004" },
                            { title: "Hey Ya! - OutKast", youtube: "PWgvGjAhvIw", year: "2003" },
                            { title: "Crazy - Gnarls Barkley", youtube: "Qe500eIK1oA", year: "2006" }
                        ],
                        myChoice: { title: "Hips Don't Lie - Shakira", youtube: "DUT5rEU6pqM", year: "2006", message: "This was playing everywhere when we were teenagers!" }
                    },
                    {
                        genre: "Smooth R&B Vibes",
                        description: "Setting a sensual, connected mood",
                        songs: [
                            { title: "Best Part - Daniel Caesar ft. H.E.R.", youtube: "iKk6_2-AAGc", year: "2017" },
                            { title: "Adorn - Miguel", youtube: "8dM5QYdTo08", year: "2012" },
                            { title: "Stay With Me - Sam Smith", youtube: "pB-5XG-DbAA", year: "2014" }
                        ],
                        myChoice: { title: "Coffee - Miguel", youtube: "8vfKUe4sQBE", year: "2015", message: "This is how I feel about us reconnecting." }
                    },
                    {
                        genre: "Feel-Good Pop Energy",
                        description: "Light, fun, and flirty",
                        songs: [
                            { title: "Levitating - Dua Lipa", youtube: "TUVcZfQe-Kw", year: "2020" },
                            { title: "Good 4 U - Olivia Rodrigo", youtube: "gNi_6U5Pm_o", year: "2021" },
                            { title: "Heat Waves - Glass Animals", youtube: "mRD0-GxqHVo", year: "2020" }
                        ],
                        myChoice: { title: "Flowers - Miley Cyrus", youtube: "G7KNmW9a75Y", year: "2023", message: "Sometimes you need to dance it out!" }
                    }
                ],
                maxiResponse: "Music has always been special to me. I'll share some of my favorites..."
            },
            
            // After Phase 1 music selection
            phase1: {
                title: "Memory Lane Soundtrack",
                subtitle: "What plays when you think about the past?",
                description: "We've talked about then and now. What music captures those feelings?",
                options: [
                    {
                        genre: "Emotional Ballads",
                        description: "For all the feelings we couldn't express",
                        songs: [
                            { title: "Someone Like You - Adele", youtube: "hLQl3WQQoQ0", year: "2011" },
                            { title: "The Scientist - Coldplay", youtube: "RB-RcX5DS5A", year: "2002" },
                            { title: "Skinny Love - Bon Iver", youtube: "ssdgFoHLwnk", year: "2007" }
                        ],
                        myChoice: { title: "Back to Black - Amy Winehouse", youtube: "TJAfLE39ZZ8", year: "2006", message: "This song understood me during my complicated times." }
                    },
                    {
                        genre: "Indie Reflection",
                        description: "Thoughtful and introspective",
                        songs: [
                            { title: "Dreams Tonite - Alvvays", youtube: "ZXu6q-6JKjA", year: "2017" },
                            { title: "Motion Sickness - Phoebe Bridgers", youtube: "9sfYpolGCu8", year: "2017" },
                            { title: "Bags - Clairo", youtube: "L9l8zCOwEII", year: "2019" }
                        ],
                        myChoice: { title: "First Day of My Life - Bright Eyes", youtube: "xUBYzpCNQ1I", year: "2005", message: "This is how today feels - like everything is starting fresh." }
                    },
                    {
                        genre: "Alternative Rock Revival",
                        description: "The soundtrack to growing up",
                        songs: [
                            { title: "Do I Wanna Know? - Arctic Monkeys", youtube: "bpOSxM0rNPM", year: "2013" },
                            { title: "Somebody Told Me - The Killers", youtube: "Y5fBdpreJiU", year: "2004" },
                            { title: "Time to Dance - The Sounds", youtube: "PIb6AZdTr-A", year: "2002" }
                        ],
                        myChoice: { title: "Electric Feel - MGMT", youtube: "MmZexg8sxyk", year: "2007", message: "This is what being around you feels like - electric." }
                    }
                ],
                maxiResponse: "Your music taste says so much about who you are. Here's what moves me..."
            },
            
            // After Phase 2 music selection
            phase2: {
                title: "Lessons in Melody",
                subtitle: "What helped you heal and grow?",
                description: "Music that got us through the tough times and made us who we are.",
                options: [
                    {
                        genre: "Empowerment Anthems",
                        description: "Songs that made us stronger",
                        songs: [
                            { title: "Survivor - Destiny's Child", youtube: "Wmc8bQoL-J0", year: "2001" },
                            { title: "Stronger - Kelly Clarkson", youtube: "Xn676-fLq7I", year: "2011" },
                            { title: "Roar - Katy Perry", youtube: "CevxZvSJLk8", year: "2013" }
                        ],
                        myChoice: { title: "Unstoppable - Sia", youtube: "h3h035Eyz5A", year: "2016", message: "This is who I became after everything - unstoppable." }
                    },
                    {
                        genre: "Healing Soul",
                        description: "Music that helped us heal",
                        songs: [
                            { title: "Rise Up - Andra Day", youtube: "lwgr_IMeEgA", year: "2015" },
                            { title: "Breathe Me - Sia", youtube: "hSH7fblcGWM", year: "2004" },
                            { title: "Fix You - Coldplay", youtube: "k4V3Mo61fJM", year: "2005" }
                        ],
                        myChoice: { title: "Hurt - Johnny Cash", youtube: "8AHCfZTRGiI", year: "2002", message: "Sometimes you need to just be still and let healing happen." }
                    },
                    {
                        genre: "Phoenix Rising",
                        description: "From the ashes, we rise",
                        songs: [
                            { title: "Shake It Out - Florence + The Machine", youtube: "WbN0nX61rIs", year: "2011" },
                            { title: "Dog Days Are Over - Florence + The Machine", youtube: "iWOyfLBYtuU", year: "2008" },
                            { title: "Elastic Heart - Sia", youtube: "KWZGAExj-es", year: "2013" }
                        ],
                        myChoice: { title: "Praying - Kesha", youtube: "v-Dur3uXXCQ", year: "2017", message: "This song represents my journey from pain to power." }
                    }
                ],
                maxiResponse: "We've both been through so much. Music helped us survive and thrive..."
            },
            
            // After Phase 3 questions - transition to cosmic phase
            phase3: {
                title: "Cosmic Prelude",
                subtitle: "Music for transcendent moments",
                description: "As we approach the deeper mysteries of connection, what sounds resonate with your soul? Choose music that bridges earth and stars.",
                options: [
                    {
                        genre: "Orchestral Ambient",
                        description: "Ethereal sounds for cosmic preparation",
                        songs: [
                            { title: "Weightless - Marconi Union", youtube: "UfcAVejslrU", year: "2011", message: "Floating into the cosmic unknown together 🌌" },
                            { title: "Music for Airports - Brian Eno", youtube: "vNwYtllyt3Q", year: "1978", message: "Where earthbound meets infinite... like us 🌍→🌌" },
                            { title: "Arrival of the Birds - The Cinematic Orchestra", youtube: "MqoANESQ4cQ", year: "2019", message: "Something magical is beginning to take flight ✨" }
                        ],
                        myChoice: { title: "Space Oddity - David Bowie", youtube: "iYYRH4apXDo", year: "1969", message: "Ground Control to Major Tom... are you ready for cosmic connection? 🚀" }
                    },
                    {
                        genre: "Mystical Depths",
                        description: "Deep, transformative sounds",
                        songs: [
                            { title: "Svefn-g-englar - Sigur Rós", youtube: "8LeQN249Jqw", year: "1999", message: "Angels speak in frequencies only the heart understands 👼" },
                            { title: "Your Hand in Mine - Explosions in the Sky", youtube: "JzIK5FaC38w", year: "2003", message: "When the universe feels alive and breathing with possibility 🌍💫" },
                            { title: "Glósóli - Sigur Rós", youtube: "Bz8iEJeh26E", year: "2005", message: "Like jumping in puddles... but the puddles are made of starlight ⭐" }
                        ],
                        myChoice: { title: "Claire de Lune - Claude Debussy", youtube: "CvFH_6DNRCY", year: "1905", message: "Moonlight sonata for two souls preparing to explore the cosmos 🌙" }
                    },
                    {
                        genre: "Emotional Ascension",
                        description: "Building toward transcendence",
                        songs: [
                            { title: "Time - Hans Zimmer", youtube: "RxabLA7UQ9k", year: "2010", message: "Every second builds toward this cosmic moment ⏰" },
                            { title: "Planet Earth - BBC Earth", youtube: "xNBNPtYjma8", year: "2006", message: "Seeing our whole world from space... perspective shift 🌍" },
                            { title: "Aqueous Transmission - Incubus", youtube: "eQK7KSTQfaw", year: "2001", message: "Floating toward something greater than ourselves 🌊" }
                        ],
                        myChoice: { title: "There Will Be Blood - Jonny Greenwood", youtube: "3M-bOr8R5vE", year: "2007", message: "The orchestra of the universe tuning up for our cosmic symphony 🎼" }
                    },
                    {
                        genre: "Interstellar Vibes",
                        description: "Music from beyond our world",
                        songs: [
                            { title: "Also sprach Zarathustra - Richard Strauss", youtube: "IFPwm0e_K98", year: "1896", message: "The dawn of something cosmic and inevitable 🌅" },
                            { title: "Interstellar Main Theme - Hans Zimmer", youtube: "4y33h81phKU", year: "2014", message: "What if heaven is just two people understanding each other perfectly? 👼" },
                            { title: "Saturn - Sleeping At Last", youtube: "h3lWwMHFhnA", year: "2013", message: "When planets align... just like we did 🪐" }
                        ],
                        myChoice: { title: "Cornfield Chase - Hans Zimmer", youtube: "SNQRiiocNx8", year: "2014", message: "Running toward infinity... together 🌾🚀" }
                    }
                ],
                maxiResponse: "I feel the universe pulling us toward something bigger... like we're about to discover what the stars have been whispering all along 🌌✨"
            },

            // After Phase 4 (cosmic connection) music selection
            phase4: {
                title: "Celestial Vibes",
                subtitle: "Music for cosmic connections",
                description: "When Air meets Water in the cosmic dance, what sounds emerge? Let the universe guide your musical selection for this mystical moment.",
                options: [
                    {
                        genre: "Ethereal Electronic",
                        description: "Cosmic soundscapes for stargazing souls",
                        songs: [
                            { title: "Midnight City - M83", youtube: "dX3k_QDnzHE", year: "2011" },
                            { title: "Wait - M83", youtube: "Xtcqk-2cMZs", year: "2011" },
                            { title: "Teardrop - Massive Attack", youtube: "u7K72X4eo_s", year: "1998" }
                        ],
                        myChoice: { title: "Heroes - David Bowie", youtube: "lXgkuM2NhYI", year: "1977", message: "Like Pisces intuition meeting Gemini curiosity among the stars ✨" }
                    },
                    {
                        genre: "Cinematic Folk",
                        description: "Earthbound spirituality meets cosmic wonder",
                        songs: [
                            { title: "Holocene - Bon Iver", youtube: "TWcyIpul8OE", year: "2011" },
                            { title: "The Night We Met - Lord Huron", youtube: "KtlgYxa6BMU", year: "2015" },
                            { title: "To Build a Home - The Cinematic Orchestra", youtube: "oUFJJNQGwhk", year: "2007" }
                        ],
                        myChoice: { title: "Cosmic Love - Florence + The Machine", youtube: "2EIeUlvHAiM", year: "2009", message: "Your cosmic love pulls me like the moon pulls the tides 🌙" }
                    },
                    {
                        genre: "Dreamy Indie",
                        description: "Atmospheric vibes for deep connections",
                        songs: [
                            { title: "River - Bishop Briggs", youtube: "h5jJtuKqTmA", year: "2016" },
                            { title: "Ribs - Lorde", youtube: "4qaeoz_7cyE", year: "2013" },
                            { title: "Tame - Pixies", youtube: "0P3lhrwio-M", year: "1989" }
                        ],
                        myChoice: { title: "Starlight - Muse", youtube: "Pgum6OT_VH8", year: "2006", message: "Far away from the memories of the people who care if I live or die - just us and the stars ⭐" }
                    },
                    {
                        genre: "Psychedelic Classics",
                        description: "Mind-expanding journeys through sound",
                        songs: [
                            { title: "Breathe - Pink Floyd", youtube: "mrojrDCI02k", year: "1973" },
                            { title: "White Rabbit - Jefferson Airplane", youtube: "WANNqr-vcx0", year: "1967" },
                            { title: "Come On Eileen - Dexys Midnight Runners", youtube: "oc-P8oDuS0Q", year: "1982" }
                        ],
                        myChoice: { title: "Aquarius - The 5th Dimension", youtube: "kjxSCAalsBE", year: "1969", message: "This is the dawning of the Age of Aquarius... and our cosmic connection 🌟" }
                    }
                ],
                maxiResponse: "The stars have aligned our musical tastes... what does this cosmic synchronicity mean? 🌌♊♓"
            },

            // After Phase 5 (intimate roleplay) music selection
            phase5: {
                title: "Seductive Scenarios",
                subtitle: "Music for fantasy and desire",
                description: "The perfect soundtrack for roleplay and intimate exploration. These songs set the mood for when imagination meets desire.",
                options: [
                    {
                        genre: "Sensual R&B",
                        description: "Smooth, seductive vibes for intimate moments",
                        songs: [
                            { title: "Earned It - The Weeknd", youtube: "waU75jdUnYw", year: "2014" },
                            { title: "Body Party - Ciara", youtube: "B9rSBcoX9ak", year: "2013" },
                            { title: "Pretty Hurts - Beyoncé", youtube: "LXXQLa-5n5w", year: "2013" }
                        ],
                        myChoice: { title: "Pony - Ginuwine", youtube: "lbnoG2dsUk0", year: "1996", message: "Classic seduction vibes... you know what this means 😏" }
                    },
                    {
                        genre: "Sultry Jazz",
                        description: "Sophisticated heat for elegant roleplay",
                        songs: [
                            { title: "I Put a Spell on You - Nina Simone", youtube: "ua2k52n_Bvw", year: "1965" },
                            { title: "Fever - Peggy Lee", youtube: "JGb5IweiYG8", year: "1958" },
                            { title: "Something to Talk About - Bonnie Raitt", youtube: "5Z8oYH_bhnA", year: "1991" }
                        ],
                        myChoice: { title: "The Way You Look Tonight - Tony Bennett", youtube: "h9ZGKALMMuc", year: "1995", message: "For when we're playing elegant strangers at that hotel bar 🍸" }
                    },
                    {
                        genre: "Electric Chemistry",
                        description: "High-energy tracks for playful scenarios",
                        songs: [
                            { title: "Dangerous Woman - Ariana Grande", youtube: "9WbCfHutDSE", year: "2016" },
                            { title: "S&M - Rihanna", youtube: "KdS6HFQ_LUc", year: "2010" },
                            { title: "Wild Thoughts - DJ Khaled ft. Rihanna", youtube: "fyaI4-5849w", year: "2017" }
                        ],
                        myChoice: { title: "Work from Home - Fifth Harmony", youtube: "5GL9JoH4Sws", year: "2016", message: "This is pure fire... perfect motivation for our elevator scenario 🔥" }
                    },
                    {
                        genre: "Intimate Indie",
                        description: "Emotional depth meets physical desire",
                        songs: [
                            { title: "Sex - The 1975", youtube: "UKIhXi-yiw8", year: "2013" },
                            { title: "Touch - Troye Sivan", youtube: "ASFW6bwDqPA", year: "2015" },
                            { title: "Golden - Harry Styles", youtube: "P3cffdsEXXw", year: "2019" }
                        ],
                        myChoice: { title: "Lost in the Light - Bahamas", youtube: "VEpMj-tqixs", year: "2014", message: "For those tender moments between the roleplay... when it gets real 💫" }
                    }
                ],
                maxiResponse: "These songs... they're making me imagine all sorts of scenarios with you. Should we explore some of them? 🎭💋"
            }
        };
        
        // Original phases data (keeping all the questions)
        this.phases = [
            {
                id: 1,
                title: "THEN & NOW",
                emoji: "🔥",
                subtitle: "From 16 to now... what's changed?",
                description: "Let's explore how we've both grown since that party 16 years ago. Some things change, some stay the same, and some get better with time.",
                questions: [
                    {
                        text: "Maxi, when we were 16 at that party, what do you remember most about that night?",
                        maleResponse: "I remember thinking you had this confidence that was different from other girls our age. And honestly, I had a bit of a crush but was too shy to do anything about it back then.",
                        options: [
                            { text: "The music and dancing", points: 3 },
                            { text: "Meeting new people", points: 5 },
                            { text: "Feeling young and free", points: 4 },
                            { text: "Specific conversations", points: 4 }
                        ]
                    },
                    {
                        text: "If 16-year-old you could see us now, heading to a spa together, what would she think?",
                        maleResponse: "She'd probably be shocked that I finally got the courage to ask you out, even if it took 16 years! And she'd be proud that I'm not the awkward teenager anymore.",
                        options: [
                            { text: "'About time!'", points: 5 },
                            { text: "'How did this happen?'", points: 3 },
                            { text: "'This is perfect'", points: 4 },
                            { text: "'Plot twist I didn't see coming'", points: 4 }
                        ]
                    },
                    {
                        text: "What's the biggest way you've changed since you were 16?",
                        maleResponse: "I've learned to be more direct about what I want and not waste time on things that don't matter. Life's too short to play games or settle for 'good enough.'",
                        options: [
                            { text: "More confident and direct", points: 5 },
                            { text: "Wiser about relationships", points: 4 },
                            { text: "Better at knowing what I want", points: 5 },
                            { text: "Less worried about others' opinions", points: 3 }
                        ]
                    },
                    {
                        text: "What's something from your teenage years that you miss?",
                        maleResponse: "The feeling that anything could happen at any moment. That excitement when you'd get ready to go out, not knowing who you'd meet or what adventure awaited.",
                        options: [
                            { text: "The endless possibilities", points: 5 },
                            { text: "Carefree summers", points: 3 },
                            { text: "Intense friendships", points: 3 },
                            { text: "Everything feeling like a big deal", points: 4 }
                        ]
                    },
                    {
                        text: "If you could give advice to your 16-year-old self, what would it be?",
                        maleResponse: "Don't wait for the 'perfect moment' to tell someone how you feel. And trust your gut - if something feels off in a relationship, it probably is.",
                        options: [
                            { text: "Trust your instincts more", points: 4 },
                            { text: "Don't wait for perfect moments", points: 5 },
                            { text: "Be more honest about feelings", points: 5 },
                            { text: "Focus on yourself first", points: 3 }
                        ]
                    }
                ]
            },
            {
                id: 2,
                title: "LIFE LESSONS",
                emoji: "😏",
                subtitle: "What we've learned the hard way...",
                description: "We've both been through relationships that shaped us. Let's talk about what we've learned and how it's made us who we are today.",
                questions: [
                    {
                        text: "How has being a mother figure to that little girl changed you?",
                        maleResponse: "I imagine it's made you incredibly strong and shown you what unconditional love really looks like. That kind of bond is rare and beautiful.",
                        options: [
                            { text: "Made me stronger", points: 5 },
                            { text: "Taught me about real love", points: 5 },
                            { text: "Changed my priorities", points: 4 },
                            { text: "Showed me my capacity to care", points: 4 }
                        ]
                    },
                    {
                        text: "What's the hardest part about your current living situation?",
                        maleResponse: "I can only imagine how strange it must be - being in a space that holds so many memories while trying to move forward. That takes incredible strength.",
                        options: [
                            { text: "Living with memories", points: 5 },
                            { text: "Feeling unsettled", points: 3 },
                            { text: "Waiting for the next chapter", points: 4 },
                            { text: "Missing what was", points: 2 }
                        ]
                    },
                    {
                        text: "After my 10-year relationship, I learned that longevity doesn't equal happiness. What's the biggest lesson your last relationship taught you?",
                        maleResponse: "Sometimes you can give everything to someone and it still isn't enough - but that doesn't mean you should give less, it means you should choose better.",
                        options: [
                            { text: "Choose better, not give less", points: 5 },
                            { text: "Trust actions over words", points: 4 },
                            { text: "Don't lose yourself in someone else", points: 4 },
                            { text: "Some people aren't ready for real love", points: 3 }
                        ]
                    },
                    {
                        text: "What's something that makes you feel most like yourself again?",
                        maleResponse: "For me, it's moments like this - good conversation, new adventures, and being around people who see the real me, not just the version shaped by past relationships.",
                        options: [
                            { text: "Deep conversations", points: 5 },
                            { text: "Physical activities", points: 2 },
                            { text: "Creative expression", points: 4 },
                            { text: "Being around the right people", points: 5 }
                        ]
                    },
                    {
                        text: "What do you think 16-year-old us would say about the paths our lives have taken?",
                        maleResponse: "I think they'd be surprised by the detours, but proud that we didn't let the tough times break us. Maybe they'd even be excited that we found our way back to each other.",
                        options: [
                            { text: "Proud we survived it all", points: 4 },
                            { text: "Surprised by the detours", points: 3 },
                            { text: "Excited about the reunion", points: 5 },
                            { text: "Amazed we're still standing", points: 3 }
                        ]
                    }
                ]
            },
            {
                id: 3,
                title: "WHAT'S NEXT",
                emoji: "🌶️",
                subtitle: "Ready for something new?",
                description: "The past shaped us, the present brought us here, but what about tomorrow? Let's explore what we're both looking for in this next chapter.",
                questions: [
                    {
                        text: "When you imagine your ideal living situation, what does it look like?",
                        maleResponse: "A place that feels like home, not just a house. Somewhere I can host people I care about, with good natural light and space for both solitude and connection.",
                        options: [
                            { text: "Cozy and welcoming", points: 5 },
                            { text: "Modern and independent", points: 3 },
                            { text: "Peaceful sanctuary", points: 4 },
                            { text: "Space for entertaining", points: 4 }
                        ]
                    },
                    {
                        text: "What kind of person do you want in your life at this stage?",
                        maleResponse: "Someone who's been through enough to appreciate genuine connection, who can handle both the fun times and the real stuff. Someone ready for depth, not just surface.",
                        options: [
                            { text: "Emotionally mature", points: 5 },
                            { text: "Ready for real connection", points: 5 },
                            { text: "Balanced and stable", points: 3 },
                            { text: "Adventurous but grounded", points: 4 }
                        ]
                    },
                    {
                        text: "If someone wanted to win your heart now, what would they need to understand about your life?",
                        maleResponse: "They'd need to understand that I come with history, but I'm not broken. I know what I want now and I won't settle for less than I deserve.",
                        options: [
                            { text: "I have history but I'm not broken", points: 5 },
                            { text: "I know what I want now", points: 5 },
                            { text: "I come as a package with responsibilities", points: 4 },
                            { text: "I need someone who's really ready", points: 4 }
                        ]
                    },
                    {
                        text: "What's something you're actually excited about for the first time in a while?",
                        maleResponse: "Honestly? This. Getting to know you again, seeing if there's still that spark I felt all those years ago, and maybe writing a completely new chapter.",
                        options: [
                            { text: "New possibilities", points: 4 },
                            { text: "This moment right now", points: 5 },
                            { text: "Starting fresh", points: 4 },
                            { text: "Rediscovering myself", points: 3 }
                        ]
                    },
                    {
                        text: "If tonight goes really well, what would you want our next adventure to be?",
                        maleResponse: "Something where we can talk for hours without interruption. Maybe a weekend somewhere new, where we can explore both the place and whatever this is between us.",
                        options: [
                            { text: "Weekend getaway", points: 5 },
                            { text: "Long dinner and deep talks", points: 4 },
                            { text: "Adventure activity together", points: 3 },
                            { text: "Quiet intimate setting", points: 4 }
                        ]
                    }
                ]
            },
            {
                id: 4,
                title: "COSMIC CONNECTION",
                emoji: "🌌",
                subtitle: "When Gemini meets Pisces... magic or chaos?",
                description: "The stars aligned to bring an Air sign and Water sign together. Let's explore what the universe has in store for this cosmic duo.",
                questions: [
                    {
                        text: "When the stars align for a perfect evening, what happens?",
                        maleResponse: "I think it's those magical moments where time stops - whether we're having deep conversations under actual stars or just getting lost in each other's thoughts.",
                        options: [
                            { text: "Deep philosophical conversations under the stars ♓", points: 4 },
                            { text: "Spontaneous adventure to three different places ♊", points: 4 },
                            { text: "A mix of both - deep talks during a midnight drive ⭐", points: 5 },
                            { text: "Netflix and analyzing the hidden meanings in movies 🎭", points: 3 }
                        ]
                    },
                    {
                        text: "Pisces intuition vs Gemini logic - how do you make decisions together?",
                        maleResponse: "I've learned that sometimes the best decisions come from combining gut feelings with practical thinking. Your intuition might catch what my analysis misses.",
                        options: [
                            { text: "Trust the gut feeling completely ♓", points: 4 },
                            { text: "Make pro/con lists and debate all options ♊", points: 4 },
                            { text: "Feel it out first, then think it through ⭐", points: 5 },
                            { text: "Flip a coin after extensive discussion 🎲", points: 3 }
                        ]
                    },
                    {
                        text: "Your cosmic communication style when things get deep:",
                        maleResponse: "I love how we can switch between playful banter and meaningful silence. Sometimes the best conversations happen without words.",
                        options: [
                            { text: "Express through art, music, or meaningful silences ♓", points: 4 },
                            { text: "Talk it out with animated gestures and multiple perspectives ♊", points: 4 },
                            { text: "Write long thoughtful messages then discuss them ⭐", points: 5 },
                            { text: "Communicate through memes and inside jokes 📱", points: 3 }
                        ]
                    },
                    {
                        text: "The universe drops you both on a desert island - what's your survival strategy?",
                        maleResponse: "I'd probably start making escape plans while you're finding the most peaceful spot on the beach. Somehow we'd end up creating the perfect balance of planning and zen.",
                        options: [
                            { text: "Build a zen meditation garden and wait for rescue ♓", points: 4 },
                            { text: "Create 15 different escape plans and signal systems ♊", points: 4 },
                            { text: "Combine intuition about weather with logical planning ⭐", points: 5 },
                            { text: "Start a philosophical island podcast 🏝️", points: 3 }
                        ]
                    },
                    {
                        text: "When Gemini's restless energy meets Pisces' need for depth:",
                        maleResponse: "It's like creating beautiful chaos that somehow works perfectly. Your depth grounds my energy, and maybe my curiosity brings out new sides of you.",
                        options: [
                            { text: "Find peace in shared silence and understanding ♓", points: 4 },
                            { text: "Keep each other endlessly entertained with new ideas ♊", points: 4 },
                            { text: "Create beautiful chaos that somehow works perfectly ⭐", points: 5 },
                            { text: "Discover that opposites really do attract 💫", points: 3 }
                        ]
                    }
                ]
            },
            {
                id: 5,
                title: "INTIMATE ROLEPLAY",
                emoji: "🎭",
                subtitle: "Let's explore some playful scenarios...",
                description: "Time to get creative and explore some fun 'what if' scenarios. These playful roleplay questions let us imagine different dynamics and desires.",
                questions: [
                    {
                        text: "Scenario: We're at a fancy hotel bar, and you're pretending we just met. What's your opening line?",
                        maleResponse: "I'd probably say something like 'I don't usually do this, but I couldn't help noticing how you light up this whole room. Mind if I buy you a drink and find out if you're as interesting as you are beautiful?'",
                        options: [
                            { text: "'Is that seat taken, or are you waiting for someone special?' 😏", points: 4 },
                            { text: "'I love your energy from across the room. I'm [fake name]' ✨", points: 5 },
                            { text: "'What's a person like you doing in a place like this?' 🍸", points: 3 },
                            { text: "Just make intense eye contact and see if you approach 👁️", points: 4 }
                        ]
                    },
                    {
                        text: "Fantasy role: You're my personal chef for the evening. What are you cooking to seduce me?",
                        maleResponse: "I'd make something that requires feeding you by hand - chocolate-covered strawberries, maybe some tapas we can share. It's not about the food, it's about the intimacy of the moment.",
                        options: [
                            { text: "Aphrodisiac feast: oysters, chocolate, wine 🦪", points: 5 },
                            { text: "Hand-fed fruits and sensual finger foods 🍓", points: 5 },
                            { text: "Cook together - pasta while dancing in kitchen 🍝", points: 4 },
                            { text: "Order takeout and focus on other appetites 😈", points: 3 }
                        ]
                    },
                    {
                        text: "Roleplay: I'm your massage therapist. Where do you want me to start, and what's your 'safe word'?",
                        maleResponse: "I'd want you to start with my shoulders and work your way down slowly. And my safe word would be... 'more please.' Wait, that doesn't sound very safe, does it?",
                        options: [
                            { text: "Start with shoulders, safe word: 'paradise' 🌴", points: 4 },
                            { text: "Begin with hands, safe word: 'breathe' 🫁", points: 3 },
                            { text: "Focus on back, safe word: 'starlight' ⭐", points: 4 },
                            { text: "Full body, safe word: 'more' (rebel alert!) 😈", points: 5 }
                        ]
                    },
                    {
                        text: "Scenario: We're trapped in an elevator for 2 hours. What's your strategy to pass the time?",
                        maleResponse: "First, I'd make sure you're comfortable. Then I'd probably suggest we play '20 questions' but with increasingly personal questions. By hour two, we'd either know everything about each other or...",
                        options: [
                            { text: "Deep conversation and philosophical discussions 🧠", points: 3 },
                            { text: "Truth or dare with escalating stakes 🎯", points: 5 },
                            { text: "Create elaborate fantasies about our escape 🗝️", points: 4 },
                            { text: "See how creative we can get in small spaces 😏", points: 5 }
                        ]
                    },
                    {
                        text: "Fantasy: You're my personal stylist. What look are you creating for me and why?",
                        maleResponse: "I'd choose something that makes you feel powerful but approachable. Something that highlights your best features while staying true to who you are. The goal is confidence.",
                        options: [
                            { text: "Elegant and sophisticated - little black dress energy 🖤", points: 4 },
                            { text: "Edgy and confident - leather jacket badass vibes 🔥", points: 5 },
                            { text: "Soft and romantic - flowing fabrics and gentle colors 🌸", points: 3 },
                            { text: "Bold and daring - something that turns heads 👀", points: 5 }
                        ]
                    }
                ]
            }
        ];

        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.showScreen('welcomeScreen');
    }
    
    setupEventListeners() {
        // Welcome screen
        document.getElementById('startButton')?.addEventListener('click', () => {
            this.startGame();
        });
        
        // Music selection - REMOVED GLOBAL LISTENER
        // Note: continueMusicButton listeners are now added dynamically in createMusicDisplay()
        // This prevents stray buttons from calling continueAfterMusic inappropriately
        
        // Phase intro screen
        document.getElementById('startPhaseButton')?.addEventListener('click', () => {
            this.startPhase();
        });
        
        // Question screen
        document.getElementById('continueButton')?.addEventListener('click', () => {
            this.nextQuestion();
        });
        
        // Phase complete screen
        document.getElementById('nextPhaseButton')?.addEventListener('click', () => {
            console.log('🎯 Static nextPhaseButton clicked');
            console.log('🔓 Evaluation unlocked:', this.isEvaluationUnlocked());

            // IMPORTANT: Only proceed if voting is completed
            if (this.isVotingCompleted()) {
                console.log('✅ Static button - Voting completed, proceeding');
                this.nextPhase();
            } else {
                console.log('❌ Static button - Voting not completed, cannot proceed');
                alert('Please complete the music evaluation by voting on Maxi\'s choice first.');
            }
        });
        
        // Results screen
        document.getElementById('restartButton')?.addEventListener('click', () => {
            this.resetGame();
        });
    }

    // Button debouncing utility
    isButtonOnCooldown(buttonId) {
        return this._buttonCooldown.has(buttonId);
    }

    setButtonCooldown(buttonId, duration = 1000) {
        this._buttonCooldown.add(buttonId);
        setTimeout(() => {
            this._buttonCooldown.delete(buttonId);
        }, duration);
    }

    startGame() {
        if (this.isButtonOnCooldown('startGame')) return;
        this.setButtonCooldown('startGame');
        this.currentPhase = 0;
        this.currentQuestion = 0;
        this.totalScore = 0;
        this.phaseScores = [0, 0, 0, 0, 0];
        this.userAnswers = [];
        this.musicSelections = [];
        this.currentMusicRound = 1; // Start with phase1 music

        // Add debug logging
        console.log('🎮 Game Starting: currentPhase=0, currentMusicRound=1');

        // Start with Phase 1 music first
        this.showMusicSelection('phase1');
    }
    
    showMusicSelection(round) {
        console.log(`🎵 === SHOW MUSIC SELECTION CALLED ===`);
        console.log(`🎵 Round: ${round}`);
        console.log(`📍 Current phase: ${this.currentPhase}`);
        console.log(`🎵 Current music round: ${this.currentMusicRound}`);

        // Validate input
        if (!round || typeof round !== 'string') {
            console.error('🚨 Invalid round parameter:', round);
            return;
        }

        // Validate round parameter against valid values
        const validRounds = ['opening', 'phase1', 'phase2', 'phase3', 'phase4', 'phase5'];
        if (!validRounds.includes(round)) {
            console.error('🚨 Invalid round value:', round, 'Expected one of:', validRounds);
            return;
        }

        const musicData = this.musicLibrary[round];

        // Validate music data exists and has required structure
        if (!musicData) {
            console.error(`🚨 Music data not found for round: ${round}`);
            console.error('Available rounds:', Object.keys(this.musicLibrary));
            return;
        }

        // Validate music data structure
        if (!musicData.options || !Array.isArray(musicData.options) || musicData.options.length === 0) {
            console.error(`🚨 Invalid music data structure for round ${round}:`, musicData);
            return;
        }

        console.log(`✅ Music data found for ${round}:`, musicData.title);

        // Reset phase data for clean music tracking
        // When showing music selection, we're starting a new musical journey for this phase
        this.currentPhaseData = {
            questions: this.currentPhaseData.questions || [],
            answers: this.currentPhaseData.answers || [],
            musicChoices: [],
            songsPlayed: [], // Always reset songs for fresh music selection
            score: this.currentPhaseData.score || 0
        };

        console.log(`🔄 Reset song tracking for new music selection (${round})`);

        // Update music selection screen content
        document.getElementById('musicTitle').textContent = musicData.title;
        document.getElementById('musicSubtitle').textContent = musicData.subtitle;
        document.getElementById('musicDescription').textContent = musicData.description;
        
        // Create genre buttons
        const genreContainer = document.getElementById('genreOptions');
        genreContainer.innerHTML = '';
        
        musicData.options.forEach((option, index) => {
            const genreCard = document.createElement('div');
            genreCard.className = 'genre-card bg-white border-2 border-gray-200 rounded-xl p-4 cursor-pointer hover:border-purple-400 transition-all duration-300';
            genreCard.innerHTML = `
                <h3 class="font-semibold text-lg mb-2">${option.genre}</h3>
                <p class="text-sm text-gray-600 mb-3">${option.description}</p>
                <div class="song-samples text-xs text-gray-500 space-y-1">
                    ${option.songs.map(song => `<div>• ${song.title}</div>`).join('')}
                </div>
            `;
            
            genreCard.addEventListener('click', () => this.selectMusicGenre(round, index));
            genreContainer.appendChild(genreCard);
        });
        
        // Show Maxi's teaser
        document.getElementById('musicResponseTeaser').textContent = musicData.maxiResponse;

        // IMPORTANT: Hide and clear ALL music player elements to prevent bypassing genre selection
        const playerContainer = document.getElementById('musicPlayerContainer');
        if (playerContainer) {
            playerContainer.classList.add('hidden');
            playerContainer.innerHTML = ''; // Clear any previous content
            console.log('🎵 Cleared music player container for fresh selection');
        }

        // Also remove any stray continue buttons that might exist
        const continueButtons = document.querySelectorAll('#continueMusicButton');
        continueButtons.forEach(button => {
            console.log('🗑️ Removing stray continue button');
            button.remove();
        });

        // Clear any existing song timers and music state
        this.clearCountdown();
        this.currentSong = null;
        this.timeRemaining = 0;

        this.showScreen('musicSelectionScreen');
    }
    
    selectMusicGenre(round, genreIndex) {
        // Validate inputs
        if (!round || typeof round !== 'string') {
            console.error('🚨 Invalid round parameter in selectMusicGenre:', round);
            return;
        }

        if (typeof genreIndex !== 'number' || genreIndex < 0) {
            console.error('🚨 Invalid genreIndex parameter in selectMusicGenre:', genreIndex);
            return;
        }

        const musicData = this.musicLibrary[round];
        if (!musicData || !musicData.options) {
            console.error(`🚨 Music data not found for round: ${round}`);
            return;
        }

        if (genreIndex >= musicData.options.length) {
            console.error(`🚨 Genre index ${genreIndex} out of bounds for round ${round}. Max index: ${musicData.options.length - 1}`);
            return;
        }

        const selectedGenre = musicData.options[genreIndex];

        // Track selected genre for this round
        this.selectedGenresByRound[round].push(genreIndex);

        // Mark selection visually
        const genreCards = document.querySelectorAll('.genre-card');
        genreCards.forEach((card, index) => {
            if (index === genreIndex) {
                card.classList.add('selected-genre');
                card.classList.add('bg-gradient-to-r', 'from-purple-50', 'to-pink-50');
            }
            card.style.pointerEvents = 'none';
        });

        // Store selection but don't start music yet
        this.selectedGenreData = selectedGenre;
        this.musicSelections.push({
            round: round,
            genre: selectedGenre.genre,
            songs: selectedGenre.songs,
            myChoice: selectedGenre.myChoice
        });

        // Setup music queue but don't start playing
        this.setupMusicQueue(selectedGenre);

        // Show music player with selected songs
        setTimeout(() => {
            this.showMusicPlayer(selectedGenre, round);
        }, 500);
    }
    
    showMusicPlayer(genreData, round) {
        // Instead of showing the song list, immediately show the popup overlay
        // This ensures the first song selection is properly tracked

        console.log('Showing popup overlay for first song selection from', genreData.genre);

        // Show popup overlay for song selection with all songs from the genre
        const allSongs = [...genreData.songs, genreData.myChoice];
        this.showSongSelectionOverlay(allSongs);

        // The music display and continue button will be shown after song selection
        // via the selectSongFromOverlay method calling showMusicDisplay()
    }
    
    continueAfterMusic() {
        console.log('🎵 continueAfterMusic called. Current round:', this.currentMusicRound);
        console.log('🎮 Current phase:', this.currentPhase);
        console.log('🎶 Songs played so far:', this.currentPhaseData.songsPlayed);
        console.log('🎵 Played songs from current genre:', this.playedSongsFromCurrentGenre);

        // CRITICAL VALIDATION: Check if we're currently showing music selection screen
        const musicSelectionScreen = document.getElementById('musicSelectionScreen');
        const playerContainer = document.getElementById('musicPlayerContainer');
        const isShowingMusicSelection = musicSelectionScreen && !musicSelectionScreen.classList.contains('hidden');
        const isPlayerHidden = playerContainer && playerContainer.classList.contains('hidden');

        if (isShowingMusicSelection && isPlayerHidden) {
            console.error('🚨 CRITICAL: continueAfterMusic called while music selection screen is active!');
            console.error('🚨 This means user is trying to bypass genre selection!');
            alert('Please select a music genre first before continuing your journey.');

            // Force clear any continue buttons and re-show selection
            const continueButtons = document.querySelectorAll('#continueMusicButton');
            continueButtons.forEach(button => {
                console.log('🗑️ Emergency removal of continue button');
                button.remove();
            });
            return;
        }

        // VALIDATION: Ensure at least one song has been selected before continuing
        if (!this.currentPhaseData.songsPlayed || this.currentPhaseData.songsPlayed.length === 0) {
            console.error('🚨 Cannot continue - no songs selected for this phase!');
            alert('Please select at least one song before continuing your journey.');
            // Re-show the music selection screen
            const musicRounds = ['phase1', 'phase2', 'phase3', 'phase4', 'phase5'];
            if (this.currentMusicRound > 0 && this.currentMusicRound <= musicRounds.length) {
                this.showMusicSelection(musicRounds[this.currentMusicRound - 1]);
            }
            return;
        }

        // Music round to phase mapping: musicRound 1 -> phase 0, musicRound 2 -> phase 1, etc.
        const targetPhase = this.currentMusicRound - 1;

        if (this.currentMusicRound >= 1 && this.currentMusicRound <= 5) {
            this.currentPhase = targetPhase;
            console.log(`🎮 Moving to Phase ${targetPhase + 1} questions (phase index ${targetPhase}). Songs from music round ${this.currentMusicRound}:`, this.currentPhaseData.songsPlayed);
            this.showPhaseIntro();
        } else {
            console.error('🚨 Unexpected currentMusicRound:', this.currentMusicRound);
            this.stopBackgroundMusic();
            this.showFinalResults();
        }
    }
    
    showPhaseIntro() {
        // Validate current phase
        if (this.currentPhase < 0 || this.currentPhase >= this.phases.length) {
            console.error(`🚨 Invalid currentPhase: ${this.currentPhase}. Valid range: 0-${this.phases.length - 1}`);
            return;
        }

        const phase = this.phases[this.currentPhase];

        // Validate phase data
        if (!phase) {
            console.error(`🚨 Phase data not found for index: ${this.currentPhase}`);
            return;
        }
        
        document.getElementById('phaseEmoji').textContent = phase.emoji;
        document.getElementById('phaseTitle').textContent = `Phase ${phase.id}: ${phase.title}`;
        document.getElementById('phaseSubtitle').textContent = phase.subtitle;
        document.getElementById('phaseDescription').querySelector('p').textContent = phase.description;
        
        // Update background gradient
        document.body.className = `min-h-screen bg-gradient-to-br gradient-phase${phase.id}`;

        console.log(`🎭 Showing Phase ${phase.id} intro: "${phase.title}"`);
        this.showScreen('phaseIntroScreen');
    }
    
    startPhase() {
        console.log(`🚀 Starting Phase ${this.currentPhase + 1} questions`);
        console.log('🎶 Songs available at phase start:', this.currentPhaseData.songsPlayed.length);

        // Disable genre switching during phase questions
        this.canSwitchGenres = false;
        this.isWaitingForPhaseTransition = false;
        this.allSongsPlayedInPhase = false;

        // DON'T preserve songs from previous selections
        // Each phase should track its own songs separately
        console.log('Starting fresh Phase', this.currentPhase + 1);

        // Preserve any songs that were already selected during music phase
        const existingSongs = this.currentPhaseData.songsPlayed || [];

        // Start fresh but preserve songs from music selection
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [...existingSongs], // Preserve songs from music phase
            score: 0
        };

        console.log('Phase started preserving', existingSongs.length, 'songs from music selection');

        this.currentQuestion = 0;

        // Keep music playing during questions
        this.showQuestion();

        // Ensure music display remains visible
        if (this.isMusicActive && this.currentPlayingSong) {
            this.updateCurrentMusicDisplay(this.currentPlayingSong);
        }
    }
    
    showQuestion() {
        const phase = this.phases[this.currentPhase];
        const question = phase.questions[this.currentQuestion];
        const questionNumber = this.currentQuestion + 1;
        const phaseQuestionTotal = phase.questions.length;

        console.log(`❓ Showing Question ${questionNumber}/${phaseQuestionTotal} from Phase ${phase.id}: "${phase.title}"`);
        
        // Update progress
        document.getElementById('phaseIndicator').textContent = `Phase ${phase.id}: ${phase.title}`;
        document.getElementById('questionCounter').textContent = `Question ${questionNumber}/${phaseQuestionTotal}`;
        
        // Calculate overall progress
        const totalQuestionsSoFar = this.currentPhase * 5 + this.currentQuestion;
        const progressPercentage = (totalQuestionsSoFar / 20) * 100;
        document.getElementById('progressBar').style.width = `${progressPercentage}%`;
        
        // Set question text
        document.getElementById('questionText').textContent = question.text;
        
        // Create answer buttons
        const answerContainer = document.getElementById('answerOptions');
        answerContainer.innerHTML = '';
        
        question.options.forEach((option, index) => {
            const button = document.createElement('button');
            button.className = 'answer-button w-full text-left p-4 bg-white border-2 border-gray-200 rounded-xl hover:border-purple-400 transition-all duration-300';
            button.textContent = option.text;
            button.dataset.points = option.points;
            button.dataset.index = index;
            
            button.addEventListener('click', (e) => this.selectAnswer(e.target));
            answerContainer.appendChild(button);
        });
        
        // Hide response section
        document.getElementById('responseSection').classList.add('hidden');
        document.getElementById('maleResponse').textContent = '';
        
        this.showScreen('questionScreen');
    }
    
    selectAnswer(button) {
        // Prevent multiple selections
        if (button.classList.contains('selected')) return;

        // Mark as selected
        button.classList.add('selected');

        // Disable all buttons
        const allButtons = document.querySelectorAll('.answer-button');
        allButtons.forEach(btn => {
            btn.disabled = true;
            btn.style.cursor = 'default';
        });

        // Record answer
        const points = parseInt(button.dataset.points);
        const phase = this.phases[this.currentPhase];
        const question = phase.questions[this.currentQuestion];

        this.phaseScores[this.currentPhase] += points;
        this.totalScore += points;

        const answerData = {
            phase: this.currentPhase + 1,
            question: this.currentQuestion + 1,
            questionText: question.text,
            answer: button.textContent,
            points: points,
            maleResponse: question.maleResponse
        };

        this.userAnswers.push(answerData);

        // Add to current phase data for summary
        this.currentPhaseData.questions.push(question.text);
        this.currentPhaseData.answers.push({
            text: button.textContent,
            points: points,
            response: question.maleResponse
        });

        // Show male response
        setTimeout(() => {
            document.getElementById('maleResponse').textContent = question.maleResponse;
            document.getElementById('responseSection').classList.remove('hidden');
        }, 500);
    }
    
    nextQuestion() {
        const phase = this.phases[this.currentPhase];
        
        if (this.currentQuestion < phase.questions.length - 1) {
            this.currentQuestion++;
            this.showQuestion();
        } else {
            this.showPhaseComplete();
        }
    }
    
    showPhaseComplete() {
        console.log('showPhaseComplete called for phase', this.currentPhase);
        const phase = this.phases[this.currentPhase];
        const phaseScore = this.phaseScores[this.currentPhase];
        const maxPhaseScore = 25; // 5 questions × 5 points
        const phasePercentage = Math.round((phaseScore / maxPhaseScore) * 100);

        // Complete current phase data
        this.currentPhaseData.score = phaseScore;

        // Check if all songs have been played in this phase
        this.checkAllSongsPlayed();

        console.log('About to show phase summary. allSongsPlayedInPhase:', this.allSongsPlayedInPhase);
        console.log('Current phase data:', this.currentPhaseData);

        // Update the static button based on evaluation status
        this.updateStaticNextPhaseButton();

        // Show phase summary instead of simple completion
        this.showPhaseSummary();
    }

    updateStaticNextPhaseButton() {
        const staticButton = document.getElementById('nextPhaseButton');
        if (staticButton) {
            const votingCompleted = this.isVotingCompleted();

            console.log('🔘 Updating nextPhaseButton:', {
                found: true,
                votingCompleted: votingCompleted,
                allSongsPlayed: this.allSongsPlayedInPhase,
                songsCount: this.currentPhaseData.songsPlayed?.length,
                testMode: this.testMode
            });

            if (votingCompleted) {
                staticButton.disabled = false;
                staticButton.className = 'w-full bg-gradient-to-r from-pink-500 to-purple-600 text-white font-semibold py-4 rounded-xl hover:from-pink-600 hover:to-purple-700 transition-all';
                staticButton.textContent = this.currentPhase === 4 ? 'Complete Journey' : 'Continue to Next Phase';
                staticButton.style.display = 'block'; // Ensure it's visible
            } else {
                // Button should not be visible if voting is not completed
                staticButton.style.display = 'none';
            }

            console.log('🔘 Updated static nextPhaseButton - voting completed:', votingCompleted);
        } else {
            console.log('🔘 nextPhaseButton not found in DOM (expected if voting not completed)');
        }
    }

    nextPhase() {
        if (this.isButtonOnCooldown('nextPhase')) {
            console.log('🚫 nextPhase on cooldown, ignoring duplicate call');
            return;
        }
        this.setButtonCooldown('nextPhase', 2000); // 2 second cooldown for phase transitions

        console.log('🏁 === NEXT PHASE CALLED ===');
        console.log('📍 Current phase before transition:', this.currentPhase);
        console.log('🎵 Current music round before transition:', this.currentMusicRound);
        console.log('💾 Saving phase data before reset:', this.currentPhaseData);
        console.log(`📊 Phase ${this.currentPhase + 1} Score: ${this.phaseScores[this.currentPhase]} points`);

        // Save the current phase data to summaries before resetting
        if (this.currentPhaseData.questions.length > 0) {
            const phase = this.phases[this.currentPhase];
            const phaseScore = this.phaseScores[this.currentPhase];
            const maxPhaseScore = 25;
            const phasePercentage = Math.round((phaseScore / maxPhaseScore) * 100);

            // Only save if not already saved
            const alreadySaved = this.phaseSummaries.some(summary =>
                summary.phaseTitle === phase.title
            );

            if (!alreadySaved) {
                this.phaseSummaries.push({
                    ...this.currentPhaseData,
                    phaseTitle: phase.title || `Phase ${this.currentPhase + 1}`, // Ensure we have a proper title
                    phaseEmoji: phase.emoji || '🎵',
                    percentage: phasePercentage
                });
                console.log('Phase summary saved for:', phase.title);
            }
        }

        // Reset phase data for the NEW phase that's about to start
        // This ensures songs selected after this point go to the new phase
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [], // Fresh for new phase
            score: 0
        };

        // Reset song tracking for new genre selection
        this.playedSongsFromCurrentGenre = [];
        this.currentGenreSongs = [];

        // Reset ALL flags for clean phase transition
        this.canSwitchGenres = true;
        this.isWaitingForPhaseTransition = false;
        this.allSongsPlayedInPhase = false;
        // Manual unlock removed - 3-song minimum enforced
        this.isShowingPhaseSummary = false; // Reset phase summary flag

        console.log('🔄 Flags reset for new phase:', {
            canSwitchGenres: this.canSwitchGenres,
            isWaitingForPhaseTransition: this.isWaitingForPhaseTransition,
            allSongsPlayedInPhase: this.allSongsPlayedInPhase,
            // manuallyUnlockedEvaluation removed - 3-song minimum enforced
            isShowingPhaseSummary: this.isShowingPhaseSummary
        });

        // Show music selection for the next phase OR final results
        // Fixed logic: currentPhase represents completed phase, so increment music round accordingly
        console.log(`🎮 Phase ${this.currentPhase + 1} questions complete, determining next step...`);

        if (this.currentPhase === 0) {
            // Phase 1 questions complete - show Phase 2 music selection
            this.currentMusicRound = 2;
            console.log(`🎵 === PHASE 1 COMPLETE - STARTING PHASE 2 MUSIC ===`);
            console.log(`🎵 Moving to music round ${this.currentMusicRound} (phase2)`);
            console.log(`📍 Current phase index: ${this.currentPhase}`);
            this.showMusicSelection('phase2');
        } else if (this.currentPhase === 1) {
            // Phase 2 questions complete - show Phase 3 music selection
            this.currentMusicRound = 3;
            console.log(`🎵 Moving to music round ${this.currentMusicRound} (phase3)`);
            this.showMusicSelection('phase3');
        } else if (this.currentPhase === 2) {
            // Phase 3 questions complete - show Phase 4 music selection
            this.currentMusicRound = 4;
            console.log(`🎵 Moving to music round ${this.currentMusicRound} (phase4)`);
            this.showMusicSelection('phase4');
        } else if (this.currentPhase === 3) {
            // Phase 4 questions complete - show Phase 5 music selection
            this.currentMusicRound = 5;
            console.log(`🎵 Phase 4 questions completed - showing Phase 5 music selection (round ${this.currentMusicRound})`);
            this.showMusicSelection('phase5');
        } else if (this.currentPhase === 4) {
            // Phase 5 questions complete - skip final results and show simple completion
            console.log('🏁 Phase 5 questions completed - showing simple completion');
            this.stopBackgroundMusic();
            this.showSimpleCompletion();
        } else {
            console.error('🚨 Unexpected phase index in nextPhase():', this.currentPhase);
            this.stopBackgroundMusic();
            this.showSimpleCompletion();
        }
    }
    
    showFinalResults() {
        // Save final phase data if not already saved
        if (this.currentPhaseData.questions.length > 0) {
            const phase = this.phases[this.currentPhase];
            const phaseScore = this.phaseScores[this.currentPhase];
            const maxPhaseScore = 25;
            const phasePercentage = Math.round((phaseScore / maxPhaseScore) * 100);

            const alreadySaved = this.phaseSummaries.some(summary =>
                summary.phaseTitle === phase.title
            );

            if (!alreadySaved) {
                this.phaseSummaries.push({
                    ...this.currentPhaseData,
                    phaseTitle: phase.title || `Phase ${this.currentPhase + 1}`, // Ensure we have a proper title
                    phaseEmoji: phase.emoji || '🎵',
                    percentage: phasePercentage
                });
                console.log('Final phase summary saved for:', phase.title);
            }
        }

        const percentage = Math.round((this.totalScore / this.maxScore) * 100);

        // Set emoji and message based on score with astrological compatibility
        let emoji, message, compatibilityTitle;
        if (percentage >= 90) {
            emoji = '💫';
            compatibilityTitle = "Cosmic Soulmates ♓💕♊";
            message = "The stars have perfectly aligned! Pisces intuition and Gemini curiosity create an unbreakable cosmic bond. Your souls dance together across the galaxy - from teenage dreams to celestial realities.";
        } else if (percentage >= 80) {
            emoji = '✨';
            compatibilityTitle = "Stellar Connection ♓⭐♊";
            message = "Jupiter blesses this union! Your Pisces depth beautifully complements Gemini's sparkling wit. The universe wrote your love story in constellations long before you met.";
        } else if (percentage >= 70) {
            emoji = '🌟';
            compatibilityTitle = "Celestial Harmony ♓🌙♊";
            message = "Venus whispers your compatibility! Pisces dreams and Gemini adventures create a magical symphony. Your astrological match transcends earthly understanding.";
        } else if (percentage >= 60) {
            emoji = '🌙';
            compatibilityTitle = "Cosmic Chemistry ♓🔮♊";
            message = "Mercury approves your connection! The mystical Fish and clever Twins share secrets the universe intended. Your compatibility sparkles like distant starlight.";
        } else {
            emoji = '🔮';
            compatibilityTitle = "Cosmic Journey ♓🌠♊";
            message = "Every constellation has its story! Pisces and Gemini may orbit differently, but your unique cosmic dance creates its own beautiful pattern in the stars.";
        }
        
        document.getElementById('finalEmoji').textContent = emoji;
        document.getElementById('finalPercentage').textContent = percentage;

        // Add compatibility title above the percentage
        const compatibilityDiv = document.createElement('div');
        compatibilityDiv.className = 'text-lg font-semibold text-purple-600 mb-2 zodiac-symbol';
        compatibilityDiv.textContent = compatibilityTitle;

        const finalPercentageElement = document.getElementById('finalPercentage');
        finalPercentageElement.parentNode.insertBefore(compatibilityDiv, finalPercentageElement);

        document.getElementById('finalMessage').querySelector('p').textContent = message;
        
        // Create comprehensive journey recap with phase summaries
        const breakdownHtml = `
            <div class="bg-gray-50 rounded-xl p-4 space-y-4 max-h-96 overflow-y-auto">
                <h3 class="font-semibold text-gray-800 mb-3">Complete Journey Recap:</h3>

                <!-- Overall Score -->
                <div class="bg-white rounded-lg p-3 border">
                    <div class="flex justify-between font-semibold text-lg">
                        <span>Total Connection Score</span>
                        <span class="text-purple-600">${this.totalScore}/${this.maxScore} points (${percentage}%)</span>
                    </div>
                </div>

                <!-- Phase by Phase Breakdown -->
                ${this.phaseSummaries.map((summary, index) => `
                    <div class="bg-white rounded-lg p-4 border">
                        <div class="flex items-center justify-between mb-3">
                            <h4 class="font-semibold flex items-center">
                                <span class="text-2xl mr-2">${summary.phaseEmoji}</span>
                                ${summary.phaseTitle}
                            </h4>
                            <span class="text-purple-600 font-bold">${summary.percentage}%</span>
                        </div>

                        <!-- Top Questions/Answers -->
                        <div class="mb-3">
                            <h5 class="text-sm font-medium text-gray-700 mb-1">Key Moments:</h5>
                            ${summary.questions.slice(0, 2).map((q, i) => `
                                <div class="text-xs text-gray-600 mb-1">
                                    <span class="font-medium">"${q.length > 60 ? q.substring(0, 60) + '...' : q}"</span>
                                    <br><span class="text-purple-600 ml-2">→ ${summary.answers[i]?.text}</span>
                                </div>
                            `).join('')}
                        </div>

                        <!-- Music -->
                        <div>
                            <h5 class="text-sm font-medium text-gray-700 mb-1">Songs Experienced:</h5>
                            <div class="flex flex-wrap gap-1">
                                ${summary.songsPlayed.map(song => `
                                    <span class="text-xs bg-purple-100 text-purple-800 px-2 py-1 rounded">${song.title.split(' - ')[0]}</span>
                                `).join('')}
                            </div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        document.getElementById('scoreBreakdown').innerHTML = breakdownHtml;
        
        // Create final playlist suggestion
        const playlistHtml = `
            <div class="bg-gradient-to-r from-blue-50 to-purple-50 rounded-xl p-4 mt-4">
                <h4 class="font-semibold text-sm mb-2">Our Road Trip Playlist:</h4>
                <p class="text-xs text-gray-600">
                    Based on your choices, I've created a special playlist for the rest of our journey.
                    It includes songs from both our selections - a perfect blend of then and now, 
                    memories and possibilities.
                </p>
                <button id="savePlaylistButton" class="mt-3 w-full bg-white text-purple-600 font-medium py-2 rounded-lg text-sm hover:bg-purple-50 transition">
                    🎵 Save Our Playlist
                </button>
            </div>
        `;
        
        // Add playlist section after score breakdown
        document.getElementById('scoreBreakdown').innerHTML += playlistHtml;

        // Add event listener for playlist save button
        setTimeout(() => {
            const saveButton = document.getElementById('savePlaylistButton');
            if (saveButton) {
                saveButton.addEventListener('click', () => {
                    this.savePlaylist();
                });
            }
        }, 100);

        // Update background
        document.body.className = 'min-h-screen bg-gradient-to-br gradient-final';

        this.showScreen('resultsScreen');
    }

    showSimpleCompletion() {
        console.log('🎯 Showing simple completion - no scoring evaluation');

        // Hide all screens
        document.querySelectorAll('.screen').forEach(screen => {
            screen.classList.add('hidden');
        });

        // Create simple completion screen instead of using complex results
        const gameContainer = document.getElementById('gameContainer');
        gameContainer.innerHTML = `
            <div class="screen fade-in">
                <div class="bg-white rounded-3xl p-8 card-shadow slide-up">
                    <div class="text-center mb-6">
                        <div class="text-7xl mb-4 emoji-pulse">🎵✨</div>
                        <h2 class="text-3xl font-bold bg-gradient-to-r from-pink-500 to-purple-600 bg-clip-text text-transparent mb-2">
                            Musical Journey Complete!
                        </h2>
                        <p class="text-gray-600 text-lg">Thanks for sharing this incredible musical adventure</p>
                    </div>

                    <div class="bg-gradient-to-r from-pink-50 to-purple-50 rounded-xl p-6 mb-6">
                        <h3 class="text-lg font-semibold mb-3 text-center">🎼 What We Discovered Together</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-gray-700">
                            <div class="text-center">
                                <div class="text-2xl mb-1">🎭</div>
                                <span>Musical preferences across different moods</span>
                            </div>
                            <div class="text-center">
                                <div class="text-2xl mb-1">🌟</div>
                                <span>Shared moments through melody</span>
                            </div>
                            <div class="text-center">
                                <div class="text-2xl mb-1">🎵</div>
                                <span>A personalized soundtrack for your journey</span>
                            </div>
                            <div class="text-center">
                                <div class="text-2xl mb-1">💫</div>
                                <span>New musical discoveries</span>
                            </div>
                        </div>
                    </div>

                    <div class="space-y-3">
                        <button id="restartJourney" class="w-full bg-gradient-to-r from-pink-500 to-purple-600 text-white font-semibold py-4 rounded-xl hover:shadow-lg transition-all duration-300">
                            Start Another Musical Journey
                        </button>
                        <p class="text-center text-xs text-gray-500 italic">
                            "Music was my refuge. I could crawl into the space between the notes and curl my back to loneliness." - Maya Angelou
                        </p>
                    </div>
                </div>
            </div>
        `;

        // Add restart functionality
        setTimeout(() => {
            const restartButton = document.getElementById('restartJourney');
            if (restartButton) {
                restartButton.addEventListener('click', () => {
                    this.restartGame();
                });
            }
        }, 100);

        // Update background for completion
        document.body.className = 'min-h-screen bg-gradient-to-br from-pink-50 to-purple-50';
    }

    // YouTube Link Availability Checker
    async checkAllVideoLinks() {
        console.log('🔗 Starting comprehensive YouTube link availability check...');

        const allVideos = this.getAllYouTubeVideos();
        const checkResults = {
            totalVideos: allVideos.length,
            availableVideos: [],
            unavailableVideos: [],
            errorVideos: [],
            duplicateCheck: [],
            generatedAt: new Date().toISOString(),
            checkDuration: 0
        };

        const startTime = Date.now();

        // Progress tracking
        let completed = 0;
        console.log(`📊 Checking ${allVideos.length} videos for availability...`);

        // Check videos in batches to avoid overwhelming the API
        const batchSize = 10;
        for (let i = 0; i < allVideos.length; i += batchSize) {
            const batch = allVideos.slice(i, i + batchSize);
            const batchPromises = batch.map(video => this.checkSingleVideoLink(video));

            try {
                const batchResults = await Promise.all(batchPromises);

                batchResults.forEach((result, index) => {
                    const video = batch[index];
                    completed++;

                    console.log(`[${completed}/${allVideos.length}] ${video.title} - ${result.status}`);

                    if (result.status === 'available') {
                        checkResults.availableVideos.push({
                            ...video,
                            checkResult: result
                        });
                    } else if (result.status === 'unavailable') {
                        checkResults.unavailableVideos.push({
                            ...video,
                            checkResult: result
                        });
                    } else {
                        checkResults.errorVideos.push({
                            ...video,
                            checkResult: result
                        });
                    }
                });

                // Small delay between batches to be respectful to YouTube
                if (i + batchSize < allVideos.length) {
                    await new Promise(resolve => setTimeout(resolve, 1000));
                }

            } catch (error) {
                console.error('Batch processing error:', error);
            }
        }

        checkResults.checkDuration = Date.now() - startTime;

        console.log('\n🎯 VIDEO AVAILABILITY CHECK COMPLETE');
        console.log('📊 SUMMARY:', {
            total: checkResults.totalVideos,
            available: checkResults.availableVideos.length,
            unavailable: checkResults.unavailableVideos.length,
            errors: checkResults.errorVideos.length,
            successRate: `${Math.round((checkResults.availableVideos.length / checkResults.totalVideos) * 100)}%`
        });

        if (checkResults.unavailableVideos.length > 0) {
            console.log('\n❌ UNAVAILABLE VIDEOS:');
            checkResults.unavailableVideos.forEach((video, index) => {
                console.log(`${index + 1}. ${video.phase} > ${video.genre} > ${video.title}`);
                console.log(`   YouTube: ${video.url}`);
                console.log(`   Issue: ${video.checkResult.error || 'Video not accessible'}`);
            });
        }

        if (checkResults.errorVideos.length > 0) {
            console.log('\n⚠️ CHECK ERRORS:');
            checkResults.errorVideos.forEach((video, index) => {
                console.log(`${index + 1}. ${video.title} - ${video.checkResult.error}`);
            });
        }

        return checkResults;
    }

    async checkSingleVideoLink(video) {
        try {
            // Method 1: Try to load video info via oEmbed API (more reliable)
            const oEmbedUrl = `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=${video.youtubeId}&format=json`;

            try {
                const response = await fetch(oEmbedUrl);
                if (response.ok) {
                    const data = await response.json();
                    return {
                        status: 'available',
                        method: 'oembed',
                        title: data.title,
                        author: data.author_name,
                        checkTimestamp: new Date().toISOString()
                    };
                } else if (response.status === 401 || response.status === 404) {
                    return {
                        status: 'unavailable',
                        method: 'oembed',
                        error: `HTTP ${response.status} - Video may be private, deleted, or restricted`,
                        checkTimestamp: new Date().toISOString()
                    };
                }
            } catch (oEmbedError) {
                // oEmbed failed, try alternative method
            }

            // Method 2: Try loading thumbnail (fallback)
            const thumbnailUrl = `https://img.youtube.com/vi/${video.youtubeId}/maxresdefault.jpg`;

            try {
                const imgResponse = await fetch(thumbnailUrl, { method: 'HEAD' });
                if (imgResponse.ok) {
                    return {
                        status: 'available',
                        method: 'thumbnail',
                        note: 'Verified via thumbnail availability',
                        checkTimestamp: new Date().toISOString()
                    };
                } else {
                    return {
                        status: 'unavailable',
                        method: 'thumbnail',
                        error: 'Thumbnail not accessible - video likely unavailable',
                        checkTimestamp: new Date().toISOString()
                    };
                }
            } catch (thumbnailError) {
                return {
                    status: 'error',
                    method: 'failed',
                    error: 'Could not verify video availability - network or CORS issue',
                    checkTimestamp: new Date().toISOString()
                };
            }

        } catch (error) {
            return {
                status: 'error',
                method: 'failed',
                error: error.message,
                checkTimestamp: new Date().toISOString()
            };
        }
    }

    async generateLinkStatusReport() {
        console.log('📄 Generating comprehensive link status report...');

        const checkResults = await this.checkAllVideoLinks();

        const reportContent = `ROAD TRIP MUSIC GAME - YOUTUBE LINK STATUS REPORT
${'='.repeat(80)}
Generated: ${checkResults.generatedAt}
Check Duration: ${(checkResults.checkDuration / 1000).toFixed(1)} seconds
Total Videos Checked: ${checkResults.totalVideos}

EXECUTIVE SUMMARY
${'-'.repeat(40)}
✅ Available Videos: ${checkResults.availableVideos.length} (${Math.round((checkResults.availableVideos.length / checkResults.totalVideos) * 100)}%)
❌ Unavailable Videos: ${checkResults.unavailableVideos.length} (${Math.round((checkResults.unavailableVideos.length / checkResults.totalVideos) * 100)}%)
⚠️ Check Errors: ${checkResults.errorVideos.length} (${Math.round((checkResults.errorVideos.length / checkResults.totalVideos) * 100)}%)

${checkResults.unavailableVideos.length === 0 ? '🎉 ALL VIDEOS ARE AVAILABLE!' : '⚠️ SOME VIDEOS NEED ATTENTION'}

UNAVAILABLE VIDEOS (NEED REPLACEMENT)
${'-'.repeat(40)}
${checkResults.unavailableVideos.length === 0 ? 'None! All videos are working properly.' :
checkResults.unavailableVideos.map((video, index) =>
    `${index + 1}. ${video.phase.toUpperCase()} > ${video.genre} > ${video.title}
   YouTube ID: ${video.youtubeId}
   URL: ${video.url}
   Issue: ${video.checkResult.error || 'Video not accessible'}
   Type: ${video.type}
   Year: ${video.year}
   ${video.message ? `Message: "${video.message}"` : ''}

   🔧 ACTION NEEDED: Find replacement video for this track`
).join('\n\n')}

AVAILABLE VIDEOS (WORKING PROPERLY)
${'-'.repeat(40)}
${checkResults.availableVideos.map((video, index) =>
    `${index + 1}. ${video.phase.toUpperCase()} > ${video.genre} > ${video.title}
   ✅ Status: Available
   📺 YouTube: ${video.url}
   🔍 Check Method: ${video.checkResult.method}
   ${video.checkResult.title ? `📝 Actual Title: "${video.checkResult.title}"` : ''}
   ${video.checkResult.author ? `👤 Channel: ${video.checkResult.author}` : ''}`
).join('\n\n')}

CHECK ERRORS (NETWORK/TECHNICAL ISSUES)
${'-'.repeat(40)}
${checkResults.errorVideos.length === 0 ? 'None! All videos were successfully checked.' :
checkResults.errorVideos.map((video, index) =>
    `${index + 1}. ${video.title}
   URL: ${video.url}
   Error: ${video.checkResult.error}
   Note: These may be temporary network issues - try checking again`
).join('\n\n')}

RECOMMENDATIONS
${'-'.repeat(40)}
${checkResults.unavailableVideos.length > 0 ? `
⚠️ HIGH PRIORITY:
• Replace ${checkResults.unavailableVideos.length} unavailable video(s) immediately
• Test replacements to ensure they match the intended mood/genre
• Update music library with new YouTube IDs
` : '✅ No immediate action needed - all videos are working!'}

${checkResults.errorVideos.length > 0 ? `
🔄 MEDIUM PRIORITY:
• Re-check ${checkResults.errorVideos.length} video(s) that had checking errors
• These might be temporary network issues
• Consider using different checking methods if errors persist
` : ''}

TECHNICAL NOTES
${'-'.repeat(40)}
• Check performed using YouTube oEmbed API and thumbnail verification
• CORS limitations may affect some checks in browser environment
• Private/unlisted videos will show as unavailable
• Age-restricted videos may show inconsistent results
• Geographically restricted content not detected by this method

${'='.repeat(80)}
End of Link Status Report

Quick Fix Template:
If videos are unavailable, use this format to replace them in the code:
{ title: "New Song - Artist", youtube: "NEW_YOUTUBE_ID", year: "YEAR" }
`;

        // Create and download the report
        const blob = new Blob([reportContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `youtube-link-status-${new Date().toISOString().split('T')[0]}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log('📄 Link status report downloaded successfully!');
        console.log('📊 Check Summary:', {
            available: checkResults.availableVideos.length,
            unavailable: checkResults.unavailableVideos.length,
            errors: checkResults.errorVideos.length,
            successRate: `${Math.round((checkResults.availableVideos.length / checkResults.totalVideos) * 100)}%`
        });

        return checkResults;
    }

    // Quick link checker for individual videos
    async quickCheckVideo(youtubeId) {
        console.log(`🔍 Quick checking video: ${youtubeId}`);

        const video = { youtubeId, title: 'Quick Check', url: `https://www.youtube.com/watch?v=${youtubeId}` };
        const result = await this.checkSingleVideoLink(video);

        console.log(`Status: ${result.status}`);
        if (result.title) console.log(`Title: ${result.title}`);
        if (result.error) console.log(`Error: ${result.error}`);

        return result;
    }

    // Background Music System
    setupMusicQueue(genreData) {
        // Store all songs from current genre for individual selection
        this.currentGenreSongs = [...genreData.songs, genreData.myChoice];
        this.playedSongsFromCurrentGenre = [];

        // Don't create a queue yet - we'll select individual songs
        this.musicQueue = [];
    }

    shuffleArray(array) {
        for (let i = array.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [array[i], array[j]] = [array[j], array[i]];
        }
    }

    startBackgroundMusic() {
        if (this.musicQueue.length === 0 || this.isPlayingBackground) return;

        this.isPlayingBackground = true;
        this.playNextSong();
    }

    playNextSong() {
        // Check if there are unplayed songs in current genre
        const unplayedSongs = this.currentGenreSongs.filter(song =>
            !this.playedSongsFromCurrentGenre.find(played => played.title === song.title)
        );

        if (unplayedSongs.length > 0) {
            // Show remaining titles from current genre
            this.showSongSelectionOverlay(unplayedSongs);
        } else {
            // No more songs in current genre - check if we can switch genres
            if (this.canSwitchGenres) {
                // We're at a phase transition, can switch genres
                this.triggerNewMusicSelection();
            } else {
                // We're mid-phase, repeat songs from current genre or wait
                this.handleMidPhaseNoSongs();
            }
        }
    }

    playYouTubeSong(song) {
        // Store current playing song
        this.currentPlayingSong = song;

        // Mark music as active
        this.isMusicActive = true;
        this.isPlayingBackground = true;

        // Update current music display
        this.updateCurrentMusicDisplay(song);

        // Create audio element for background playback
        // Note: Due to YouTube's restrictions, we'll simulate with a timer
        // In a real implementation, you'd need YouTube API or use preview tracks
        this.simulateSongPlayback(song);
    }

    simulateSongPlayback(song) {
        // Simulate song duration (2-3 minutes like real songs)
        const duration = Math.random() * 60000 + 120000; // 2-3 minutes (120-180 seconds)
        this.timeRemaining = Math.floor(duration / 1000); // Convert to seconds
        this.songDuration = this.timeRemaining; // Store initial duration

        // Start countdown
        this.startCountdown();

        this.currentSongTimer = setTimeout(() => {
            if (this.isMusicActive) {
                this.playNextSong();
            }
        }, duration);
    }

    startCountdown() {
        this.clearCountdown();

        console.log('Starting countdown with', this.timeRemaining, 'seconds');

        this.countdownInterval = setInterval(() => {
            this.timeRemaining--;
            this.updateCurrentMusicDisplay(null, true); // Update with countdown

            // Also update the music player container if it exists
            const countdownElement = document.getElementById('countdown');
            const progressElement = document.getElementById('timerProgress');

            if (countdownElement) {
                countdownElement.textContent = `${this.timeRemaining}s`;
            }

            if (progressElement) {
                const totalDuration = this.songDuration || 120;
                const progressPercent = (this.timeRemaining / totalDuration) * 100;
                progressElement.style.width = `${progressPercent}%`;
            }

            if (this.timeRemaining <= 0) {
                this.clearCountdown();
                // When countdown reaches 0, trigger next song (which may trigger music selection)
                if (this.isMusicActive) {
                    this.isManualSelection = false; // Mark as automatic when countdown expires
                    this.playNextSong();
                }
            }
        }, 1000); // This should be 1000ms = 1 second
    }

    clearCountdown() {
        if (this.countdownInterval) {
            clearInterval(this.countdownInterval);
            this.countdownInterval = null;
        }
        // Also clear any pending timer
        if (this.currentSongTimer) {
            clearTimeout(this.currentSongTimer);
            this.currentSongTimer = null;
        }
    }

    formatTime(seconds) {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
    }

    stopBackgroundMusic() {
        this.isPlayingBackground = false;
        this.isMusicActive = false;
        this.clearCountdown();
        if (this.currentSongTimer) {
            clearTimeout(this.currentSongTimer);
            this.currentSongTimer = null;
        }
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }
        this.hideMusicDisplay();
    }

    triggerNewMusicSelection() {
        console.log('Triggering new music selection. canSwitchGenres:', this.canSwitchGenres);

        // Only allow genre switching at proper transitions
        if (!this.canSwitchGenres) {
            console.log('Cannot switch genres mid-phase. Waiting for phase transition.');
            this.handleMidPhaseNoSongs();
            return;
        }

        // Reset played songs for new genre
        this.playedSongsFromCurrentGenre = [];
        this.currentGenreSongs = [];
        this.canSwitchGenres = false;

        // Show new genre selection from current round
        this.showMidPhaseNewMusicSelection();
    }

    showMidPhaseNewMusicSelection() {
        // Get current music round data
        const currentRound = this.getCurrentMusicRound();
        if (!currentRound) return;

        // Don't pause the music, just show the overlay
        // Music continues in background during selection

        // Show music selection overlay
        this.createMusicSelectionOverlay(currentRound);
    }

    getCurrentMusicRound() {
        // Determine which music round we're in based on current phase
        if (this.currentMusicRound === 1) return 'phase1';
        if (this.currentMusicRound === 2) return 'phase2';
        if (this.currentMusicRound === 3) return 'phase3';
        if (this.currentMusicRound === 4) return 'phase4';
        if (this.currentMusicRound === 5) return 'phase5';
        return 'phase1'; // Default to phase1 (no opening)
    }

    getMusicPhaseDisplayName(musicPhase) {
        const displayNames = {
            'phase1': 'Memory Lane Soundtrack',
            'phase2': 'Lessons in Melody',
            'phase3': 'Cosmic Prelude',
            'phase4': 'Celestial Vibes',
            'phase5': 'Seductive Scenarios'
        };
        return displayNames[musicPhase] || musicPhase;
    }

    createMusicSelectionOverlay(round) {
        const musicData = this.musicLibrary[round];

        // Filter out already selected genres for this round
        const selectedIndexes = this.selectedGenresByRound[round] || [];
        const availableOptions = musicData.options.filter((option, index) =>
            !selectedIndexes.includes(index)
        );

        // If no options available, reset and show all options
        if (availableOptions.length === 0) {
            this.selectedGenresByRound[round] = [];
            availableOptions.push(...musicData.options);
        }

        // Create overlay
        const overlay = document.createElement('div');
        overlay.id = 'musicSelectionOverlay';
        overlay.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50';

        const remainingText = selectedIndexes.length > 0 ?
            `<p class="text-xs text-purple-600 mb-2">${availableOptions.length} remaining genres</p>` : '';

        overlay.innerHTML = `
            <div class="bg-white rounded-xl p-6 max-w-md mx-4 slide-up">
                <div class="text-center mb-4">
                    <div class="text-2xl mb-2">🎵</div>
                    <h3 class="text-lg font-semibold">${musicData.title}</h3>
                    <p class="text-sm text-gray-600">${musicData.subtitle}</p>
                    ${remainingText}
                </div>

                <p class="text-sm text-gray-700 mb-4">${musicData.description}</p>

                <div class="space-y-3" id="overlayGenreOptions">
                    ${availableOptions.map((option, index) => `
                        <div class="genre-option-overlay bg-gray-50 border-2 border-gray-200 rounded-lg p-3 cursor-pointer hover:border-purple-400 transition-all duration-300" data-index="${index}">
                            <h4 class="font-semibold text-sm">${option.genre}</h4>
                            <p class="text-xs text-gray-600">${option.description}</p>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        // Add click handlers with available options context and single-click protection
        const genreOptions = overlay.querySelectorAll('.genre-option-overlay');
        genreOptions.forEach((option, index) => {
            option.addEventListener('click', (e) => {
                // Prevent double-clicks and event propagation
                e.stopPropagation();
                e.preventDefault();

                // Disable all options to prevent multiple clicks
                genreOptions.forEach(opt => {
                    opt.style.pointerEvents = 'none';
                    opt.classList.add('opacity-50');
                });

                // Select genre and remove overlay immediately
                this.selectOverlayMusicGenre(round, index, availableOptions);
                this.removeOverlay();
            });
        });
    }

    selectOverlayMusicGenre(round, genreIndex, availableOptions) {
        // Get the actual genre from available options (filtered list)
        const selectedGenre = availableOptions[genreIndex];

        // Find the original index in the full music library to track selection
        const musicData = this.musicLibrary[round];
        const originalIndex = musicData.options.findIndex(option => option.genre === selectedGenre.genre);

        // Track selected genre for this round
        this.selectedGenresByRound[round].push(originalIndex);

        // Update selected genre data
        this.selectedGenreData = selectedGenre;

        // Setup new music queue
        this.setupMusicQueue(selectedGenre);

        // Resume background music
        this.isPlayingBackground = true;
        this.playNextSong();

        // Show brief notification
        this.showMusicChangeNotification(selectedGenre.genre);
    }

    showMusicChangeNotification(genreName) {
        const notification = document.createElement('div');
        notification.className = 'fixed top-4 left-1/2 transform -translate-x-1/2 bg-purple-600 text-white px-4 py-2 rounded-lg shadow-lg z-50';
        notification.innerHTML = `🎵 Now playing: ${genreName}`;

        document.body.appendChild(notification);

        setTimeout(() => {
            notification.remove();
        }, 3000);
    }

    removeOverlay() {
        console.log('Attempting to remove overlays...');

        const overlay = document.getElementById('musicSelectionOverlay');
        if (overlay) {
            console.log('Removing musicSelectionOverlay');
            overlay.remove();
        }

        const songOverlay = document.getElementById('songSelectionOverlay');
        if (songOverlay) {
            console.log('Removing songSelectionOverlay');
            songOverlay.remove();
        }

        // Also remove any remaining overlays by class
        const allOverlays = document.querySelectorAll('[id*="SelectionOverlay"]');
        allOverlays.forEach(overlay => {
            console.log('Removing overlay by selector:', overlay.id);
            overlay.remove();
        });

        console.log('Overlay removal completed');
    }

    triggerManualMusicSelection() {
        console.log('Manual music selection triggered');

        // Mark next selection as automatic (triggered by countdown)
        this.isManualSelection = false;

        // Stop current song and countdown
        this.clearCountdown();
        if (this.currentSongTimer) {
            clearTimeout(this.currentSongTimer);
            this.currentSongTimer = null;
        }

        // Hide current music display temporarily during selection
        this.hideMusicDisplay();

        // Trigger next song selection (remaining titles or new genre)
        this.playNextSong();
    }

    showSongSelectionOverlay(availableSongs) {
        // Create overlay for individual song selection
        const overlay = document.createElement('div');
        overlay.id = 'songSelectionOverlay';
        overlay.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50';

        const remainingText = this.playedSongsFromCurrentGenre.length > 0 ?
            `<p class="text-xs text-purple-600 mb-2">${availableSongs.length} remaining songs from this genre</p>` : '';

        overlay.innerHTML = `
            <div class="bg-white rounded-xl p-6 max-w-md mx-4 slide-up">
                <div class="text-center mb-4">
                    <div class="text-2xl mb-2">🎵</div>
                    <h3 class="text-lg font-semibold">Choose Your Next Song</h3>
                    <p class="text-sm text-gray-600">From ${this.selectedGenreData ? this.selectedGenreData.genre : 'current genre'}</p>
                    ${remainingText}
                </div>

                <div class="space-y-3" id="overlaySongOptions">
                    ${availableSongs.map((song, index) => `
                        <div class="song-option-overlay bg-gray-50 border-2 border-gray-200 rounded-lg p-3 cursor-pointer hover:border-purple-400 transition-all duration-300" data-index="${index}">
                            <h4 class="font-semibold text-sm">${song.title}</h4>
                            <p class="text-xs text-gray-600">${song.year ? `Released: ${song.year}` : ''}</p>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        // Add click handlers with single-click protection
        const songOptions = overlay.querySelectorAll('.song-option-overlay');
        songOptions.forEach((option, index) => {
            option.addEventListener('click', (e) => {
                // Prevent double-clicks and event propagation
                e.stopPropagation();
                e.preventDefault();

                // Disable all options to prevent multiple clicks
                songOptions.forEach(opt => {
                    opt.style.pointerEvents = 'none';
                    opt.classList.add('opacity-50');
                });

                // Ensure this is marked as a manual selection
                this.isManualSelection = true;

                // Select song and remove overlay immediately
                this.selectSongFromOverlay(availableSongs[index]);
                this.removeOverlay();
            });
        });
    }

    selectSongFromOverlay(selectedSong) {
        console.log('Selecting song from overlay:', selectedSong.title);
        console.log('Current phase:', this.currentPhase, 'Music round:', this.currentMusicRound);
        console.log('Current phase data before adding:', this.currentPhaseData.songsPlayed);

        // Check if this song was already selected (prevent double processing)
        const alreadyPlayed = this.playedSongsFromCurrentGenre.find(song => song.title === selectedSong.title);
        if (alreadyPlayed) {
            console.log('Song already selected, skipping...');
            return;
        }

        // Remove the overlay immediately after selection
        this.removeOverlay();

        // Mark song as played
        this.playedSongsFromCurrentGenre.push(selectedSong);

        // Determine which music phase this song belongs to
        const musicPhase = this.getCurrentMusicRound();
        console.log(`Adding song to music phase: ${musicPhase} (currentMusicRound: ${this.currentMusicRound}, currentPhase: ${this.currentPhase})`);

        // Save to the appropriate music phase summary
        if (!this.musicPhaseSummaries[musicPhase]) {
            this.musicPhaseSummaries[musicPhase] = {
                phaseName: this.getMusicPhaseDisplayName(musicPhase),
                songsPlayed: []
            };
        }

        const songData = {
            title: selectedSong.title,
            genre: this.selectedGenreData ? this.selectedGenreData.genre : 'Unknown',
            youtubeId: selectedSong.youtube, // Store the YouTube ID directly
            timestamp: new Date().toISOString(),
            userSelected: this.isManualSelection // Mark based on selection method
        };

        // Use centralized song tracking method
        this.addSongToTracking(songData, musicPhase);

        console.log(`🎵 Song marked as ${this.isManualSelection ? 'USER-SELECTED' : 'AUTO-SELECTED'}:`, selectedSong.title);
        console.log(`🎶 Saved to music phase: ${musicPhase}`);

        // Reset to manual for next selection (assume manual unless set otherwise)
        this.isManualSelection = true;

        // Automatically open YouTube link (only once)
        if (selectedSong.youtube) {
            const youtubeUrl = `https://www.youtube.com/watch?v=${selectedSong.youtube}`;
            console.log('Opening YouTube URL:', youtubeUrl);
            window.open(youtubeUrl, '_blank');
        } else {
            console.warn('No YouTube ID found for song:', selectedSong.title);
            // Still show notification but mention no YouTube available
            this.showMusicChangeNotification(`Playing: ${selectedSong.title} (No YouTube link available)`);
        }

        // Start playing the selected song
        this.playYouTubeSong(selectedSong);

        // Show brief notification
        this.showMusicChangeNotification(`Now playing: ${selectedSong.title}`);

        // If this is the first song selection from initial genre selection, show music display
        if (this.playedSongsFromCurrentGenre.length === 1) {
            console.log('First song selected - showing music display');
            console.log('Creating display for opening round:', this.currentMusicRound === 0);
            this.createMusicDisplay();
        }

        // Check if all songs have been played
        this.checkAllSongsPlayed();

        // If we're currently showing the phase summary (scoring page), refresh it to show the new song
        const phaseCompleteScreen = document.getElementById('phaseCompleteScreen');
        const isPhaseCompleteVisible = phaseCompleteScreen && !phaseCompleteScreen.classList.contains('hidden');
        console.log('Checking if should refresh phase summary. isShowingPhaseSummary:', this.isShowingPhaseSummary, 'phaseCompleteVisible:', isPhaseCompleteVisible);

        if (this.isShowingPhaseSummary || isPhaseCompleteVisible) {
            console.log('Refreshing phase summary to show new song');
            this.showPhaseSummary();
        }
    }

    createMusicDisplay() {
        console.log('Creating music display with continue button');

        const playerContainer = document.getElementById('musicPlayerContainer');
        if (playerContainer) {
            const currentSong = this.currentSong || this.playedSongsFromCurrentGenre[this.playedSongsFromCurrentGenre.length - 1];

            playerContainer.innerHTML = `
                <div class="mt-6 space-y-4 slide-up">
                    <div class="bg-gradient-to-r from-purple-50 to-pink-50 rounded-xl p-4">
                        <div class="text-center mb-4">
                            <div class="text-2xl mb-2">🎵</div>
                            <h3 class="font-semibold text-lg mb-1">Now Playing</h3>
                            <p class="text-purple-700 font-medium">${currentSong?.title || 'Current Song'}</p>
                            <p class="text-sm text-gray-600">${this.selectedGenreData?.genre || 'Selected Genre'}</p>
                        </div>

                        <div class="bg-white rounded-lg p-3 mb-3">
                            <div class="flex justify-between items-center mb-2">
                                <span class="text-sm text-gray-600">Next song in:</span>
                                <span id="countdown" class="font-medium text-purple-600">${this.timeRemaining || this.songDuration || 30}s</span>
                            </div>
                            <div class="w-full bg-gray-200 rounded-full h-2">
                                <div id="timerProgress" class="bg-gradient-to-r from-pink-500 to-purple-600 h-2 rounded-full transition-all duration-1000" style="width: 100%"></div>
                            </div>
                        </div>

                        <div class="flex space-x-2">
                            <button id="skipSongButton" class="flex-1 bg-gray-200 hover:bg-gray-300 text-gray-700 font-medium py-2 px-4 rounded-lg transition-all">
                                ⏭️ Skip Song
                            </button>
                            <button id="continueMusicButton" class="flex-1 bg-gradient-to-r from-pink-500 to-purple-600 text-white font-semibold py-2 px-4 rounded-lg hover:from-pink-600 hover:to-purple-700 transition-all">
                                Continue Journey
                            </button>
                        </div>
                    </div>
                </div>
            `;

            playerContainer.classList.remove('hidden');

            // The countdown is already running from simulateSongPlayback
            // No need to start another one here
            console.log('Music display created. Countdown already running with', this.timeRemaining, 'seconds');

            // Add skip button event listener
            document.getElementById('skipSongButton')?.addEventListener('click', () => {
                console.log('Skip song button clicked');
                this.isManualSelection = false; // Mark next selection as automatic
                this.clearCountdown();
                this.playNextSong();
            });

            // Add continue button event listener
            document.getElementById('continueMusicButton')?.addEventListener('click', () => {
                console.log('Continue Journey button clicked');
                this.continueAfterMusic();
            });
        }
    }


    handleMidPhaseNoSongs() {
        console.log('No more songs available mid-phase. Options: repeat or pause.');

        // Option 1: Repeat songs from current genre
        if (this.currentGenreSongs.length > 0) {
            console.log('Repeating songs from current genre');

            // Show brief notification about repeating
            this.showMusicChangeNotification('🔁 Replaying songs from this genre');

            // Reset played songs to allow repetition
            this.playedSongsFromCurrentGenre = [];

            // Small delay to show the notification
            setTimeout(() => {
                this.showSongSelectionOverlay(this.currentGenreSongs);
            }, 1000);
        } else {
            // Option 2: Show a waiting message until phase transition
            this.showWaitingForPhaseMessage();
        }
    }

    showWaitingForPhaseMessage() {
        // Update music display to show waiting state
        this.isWaitingForPhaseTransition = true;

        let musicDisplay = document.getElementById('currentMusicDisplay');
        if (!musicDisplay) {
            musicDisplay = document.createElement('div');
            musicDisplay.id = 'currentMusicDisplay';
            musicDisplay.className = 'fixed top-4 right-4 bg-black bg-opacity-75 text-white px-3 py-2 rounded-lg shadow-lg z-50 max-w-80';
            document.body.appendChild(musicDisplay);
        }

        musicDisplay.innerHTML = `
            <div class="flex items-center space-x-2">
                <div class="animate-pulse">
                    <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                    </svg>
                </div>
                <div class="text-xs flex-1">
                    <div class="font-semibold">Music paused</div>
                    <div class="opacity-75">Complete phase to continue</div>
                    <button id="repeatSongsButton" class="bg-purple-600 hover:bg-purple-700 px-2 py-1 rounded text-xs mt-1 transition-colors">
                        🔁 Replay songs
                    </button>
                </div>
            </div>
        `;

        // Add event listener to repeat button
        const repeatButton = document.getElementById('repeatSongsButton');
        if (repeatButton) {
            repeatButton.addEventListener('click', () => {
                this.handleMidPhaseNoSongs();
            });
        }
    }

    createMaxiVotingSection() {
        // Get Maxi's choice for current music phase
        const currentMusicPhase = this.getCurrentMusicRound();
        const musicData = this.musicLibrary[currentMusicPhase];

        if (!musicData) {
            return `<div class="text-sm">No music data available for voting.</div>`;
        }

        // Find Maxi's choice from the selected genre
        let maxiChoice = null;
        if (this.selectedGenreData && musicData.options) {
            const selectedGenre = musicData.options.find(option => option.genre === this.selectedGenreData.genre);
            if (selectedGenre && selectedGenre.myChoice) {
                maxiChoice = selectedGenre.myChoice;
            }
        }

        // Check if voting is already completed for this phase
        const votingCompleted = this.maxiVotingResults && this.maxiVotingResults[currentMusicPhase];

        if (votingCompleted) {
            // Show results
            return this.createVotingResultsSection(maxiChoice, votingCompleted);
        } else {
            // Show voting interface
            return this.createVotingInterface(maxiChoice, currentMusicPhase);
        }
    }

    createVotingInterface(maxiChoice, currentMusicPhase) {
        return `
            <div id="maxiVotingSection">
                <div class="text-center mb-4">
                    <div class="text-2xl mb-2">🗳️</div>
                    <h4 class="font-semibold text-gray-800">Maxi's Turn to Vote!</h4>
                    <p class="text-sm text-gray-600">After listening to all songs, which one did Maxi prefer?</p>
                </div>

                <div class="space-y-2 mb-4">
                    ${this.currentPhaseData.songsPlayed.map((song, index) => `
                        <button class="maxi-vote-btn w-full text-left p-3 bg-white border-2 border-gray-200 rounded-lg hover:border-purple-400 transition-all duration-300"
                                data-song-index="${index}" data-music-phase="${currentMusicPhase}">
                            <div class="flex justify-between items-center">
                                <div>
                                    <span class="font-medium text-sm">${song.title}</span>
                                    <div class="text-xs text-gray-500">${song.genre}</div>
                                </div>
                                <div class="text-purple-600">🎵</div>
                            </div>
                        </button>
                    `).join('')}
                </div>

                <div class="text-center">
                    <p class="text-xs text-gray-500">Click on Maxi's preferred song to see his choice!</p>
                </div>
            </div>
        `;
    }

    createVotingResultsSection(maxiChoice, votingResult) {
        const userGuessedCorrect = votingResult.userChoice === votingResult.maxiActualChoice;

        return `
            <div id="maxiVotingResults" class="slide-up">
                <div class="text-center mb-4">
                    <div class="text-2xl mb-2">${userGuessedCorrect ? '🎉' : '💭'}</div>
                    <h4 class="font-semibold text-gray-800">
                        ${userGuessedCorrect ? 'You Know Me So Well!' : 'Maxi\'s Real Choice'}
                    </h4>
                </div>

                <!-- Show user's guess vs Maxi's actual choice -->
                <div class="space-y-3 mb-4">
                    <div class="p-3 bg-blue-50 border-2 border-blue-200 rounded-lg">
                        <div class="text-xs text-blue-600 font-medium">Your Guess:</div>
                        <div class="font-medium">${votingResult.userChoice}</div>
                    </div>

                    <div class="p-3 bg-purple-50 border-2 border-purple-200 rounded-lg">
                        <div class="text-xs text-purple-600 font-medium">Tom's Actual Favorite:</div>
                        <div class="font-medium">${maxiChoice ? maxiChoice.title : 'Unknown'}</div>
                        ${maxiChoice && maxiChoice.message ? `
                            <div class="mt-2 p-2 bg-white rounded text-sm italic text-gray-700">
                                💭 "${maxiChoice.message}"
                            </div>
                        ` : ''}
                    </div>
                </div>

                ${userGuessedCorrect ? `
                    <div class="text-center p-3 bg-green-50 border border-green-200 rounded-lg">
                        <div class="text-green-800 font-medium">✨ Perfect Match! ✨</div>
                        <div class="text-sm text-green-700">You really understand my taste!</div>
                    </div>
                ` : `
                    <div class="text-center p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                        <div class="text-yellow-800 font-medium">🤔 Different Vibes</div>
                        <div class="text-sm text-yellow-700">But that's what makes us interesting!</div>
                    </div>
                `}
            </div>
        `;
    }

    checkAllSongsPlayed() {
        // For now, require at least 3 unique songs to be played in the phase
        // But in test mode, only require 1 song
        if (!this.currentPhaseData.songsPlayed || !Array.isArray(this.currentPhaseData.songsPlayed)) {
            console.warn('Invalid songsPlayed data, resetting to empty array');
            this.currentPhaseData.songsPlayed = [];
        }

        // Use normalized titles for uniqueness check
        const uniqueSongs = new Set(this.currentPhaseData.songsPlayed
            .filter(song => song && song.title)
            .map(song => song.title.toLowerCase().trim()));
        const wasAllSongsPlayed = this.allSongsPlayedInPhase;
        const requiredSongs = this.testMode ? 1 : 3;
        this.allSongsPlayedInPhase = uniqueSongs.size >= requiredSongs;

        console.log(`Songs played in phase: ${uniqueSongs.size}/${requiredSongs} required`, Array.from(uniqueSongs));

        // If the phase is completed and we have enough songs, update the summary display
        if (!wasAllSongsPlayed && this.allSongsPlayedInPhase && this.currentPhaseData.questions.length === 5) {
            console.log('All songs now played and phase completed - updating summary');
            this.updatePhaseSummaryDisplay();
        }
        // If we're already showing the phase summary, update the status
        else if (this.isShowingPhaseSummary) {
            console.log('Updating existing phase summary display');
            this.updatePhaseSummaryDisplay();
        }
    }

    showPhaseSummary() {
        console.log('showPhaseSummary called');
        this.isShowingPhaseSummary = true;

        const phase = this.phases[this.currentPhase];
        const phaseScore = this.phaseScores[this.currentPhase];
        const maxPhaseScore = 25; // 5 questions × 5 points
        const phasePercentage = Math.round((phaseScore / maxPhaseScore) * 100);

        // Update phase score
        this.currentPhaseData.score = phaseScore;

        // Don't save to phaseSummaries here - it will be saved in nextPhase()
        // This prevents duplication

        // Create summary screen content
        const summaryHtml = `
            <div class="bg-white rounded-xl p-6 shadow-lg max-w-2xl mx-auto">
                <div class="text-center mb-6">
                    <div class="text-4xl mb-2">${phase.emoji}</div>
                    <h2 class="text-2xl font-bold text-gray-800">Phase ${phase.id}: ${phase.title}</h2>
                    <div class="text-3xl font-bold text-purple-600 mt-2">${phasePercentage}%</div>
                    <p class="text-gray-600">Connection Score</p>
                </div>

                <div class="space-y-6">
                    <!-- Questions & Answers Summary -->
                    <div class="bg-gray-50 rounded-lg p-4">
                        <h3 class="font-semibold text-lg mb-3 flex items-center">
                            💭 Our Conversation
                        </h3>
                        <div class="space-y-3 max-h-40 overflow-y-auto">
                            ${this.currentPhaseData.questions.map((q, i) => `
                                <div class="text-sm">
                                    <p class="font-medium text-gray-700">"${q}"</p>
                                    <p class="text-purple-600 ml-4">→ ${this.currentPhaseData.answers[i]?.text}</p>
                                </div>
                            `).join('')}
                        </div>
                    </div>

                    <!-- Music Summary with Maxi's Voting -->
                    <div class="bg-gradient-to-r from-purple-50 to-pink-50 rounded-lg p-4">
                        <h3 class="font-semibold text-lg mb-3 flex items-center">
                            🎵 Musical Journey
                        </h3>

                        ${this.isEvaluationUnlocked() ? this.createMaxiVotingSection() : `
                            <div class="text-sm space-y-1">
                                ${this.currentPhaseData.songsPlayed.map(song => `
                                    <div class="flex justify-between">
                                        <span>${song.title}</span>
                                        <span class="text-gray-500">${song.genre}</span>
                                    </div>
                                `).join('')}
                            </div>
                        `}

                        <div class="mt-3 p-2 rounded ${this.isVotingCompleted() ? 'bg-green-100 text-green-800' : this.isEvaluationUnlocked() ? 'bg-blue-100 text-blue-800' : 'bg-orange-100 text-orange-800'}">
                            ${this.isVotingCompleted()
                                ? '✅ Evaluation complete - Ready to continue!'
                                : this.isEvaluationUnlocked()
                                ? '🗳️ Songs complete - Now vote on Maxi\'s choice!'
                                : '⏳ Experience more songs to unlock evaluation'}
                        </div>
                    </div>
                </div>

                <div class="mt-4 text-center">
                    ${this.isVotingCompleted() ? `
                        <div class="text-sm text-gray-600 italic">
                            ✅ Evaluation complete! Ready to continue.
                        </div>
                    ` : this.isEvaluationUnlocked() ? `
                        <div class="text-sm text-gray-600 italic">
                            🗳️ Vote on Maxi's choice above to unlock continue button.
                        </div>
                    ` : `
                        <div class="mt-3 space-y-2">
                            <p class="text-sm text-gray-600 mb-2">You must listen to at least 3 different songs to unlock the evaluation.</p>
                            <div class="flex gap-2 justify-center">
                                <button id="skipNextSong" class="px-4 py-2 bg-blue-100 hover:bg-blue-200 text-blue-800 rounded-lg font-medium text-sm transition-all">
                                    Continue Listening
                                </button>
                            </div>
                        </div>
                    `}
                </div>

                <!-- Continue Button - Only show after voting is completed -->
                ${this.isVotingCompleted() ? `
                    <button id="nextPhaseButton"
                            class="w-full bg-gradient-to-r from-pink-500 to-purple-600 text-white font-semibold py-4 rounded-xl hover:from-pink-600 hover:to-purple-700 transition-all mt-6">
                        ${this.currentPhase === 4 ? 'Complete Journey' : 'Continue to Next Phase'}
                    </button>
                ` : ''}
            </div>
        `;

        // Update the phase complete screen
        const phaseScreen = document.getElementById('phaseCompleteScreen');
        console.log('Updating phase complete screen:', phaseScreen ? 'found' : 'not found');

        if (phaseScreen) {
            phaseScreen.innerHTML = summaryHtml;
            console.log('Phase summary HTML updated');
        }

        console.log('Showing phase summary screen...');
        this.showScreen('phaseCompleteScreen');

        // Re-attach event listener
        const nextButton = document.getElementById('nextPhaseButton');
        console.log('Next phase button:', nextButton ? 'found' : 'not found', 'allSongsPlayed:', this.allSongsPlayedInPhase);

        if (nextButton) {
            // Remove any existing event listeners by cloning
            const newButton = nextButton.cloneNode(true);
            nextButton.parentNode.replaceChild(newButton, nextButton);

            // Add fresh event listener
            newButton.addEventListener('click', () => {
                console.log('🎯 Next phase button clicked from phase summary');
                console.log('📍 Current phase:', this.currentPhase);
                console.log('🔓 allSongsPlayedInPhase:', this.allSongsPlayedInPhase);
                console.log('🔓 evaluationUnlocked:', this.isEvaluationUnlocked());

                if (this.isVotingCompleted()) {
                    console.log('✅ Voting completed - proceeding to next phase');
                    this.nextPhase();
                } else {
                    console.log('❌ Cannot proceed - voting not completed yet');
                    alert('Please complete the music evaluation by voting on Maxi\'s choice first.');
                }
            });

            // Update button state to enabled/disabled based on evaluation status
            this.updateStaticNextPhaseButton();
        }

        // Attach Maxi voting event listeners
        this.attachMaxiVotingEventListeners();

        // Manual skip to evaluation removed - users must listen to 3 songs

        // Attach skip next song listener
        this.attachSkipNextSongListener();
    }

    // Manual skip to evaluation removed - enforcing 3-song minimum

    // Centralized song tracking method
    addSongToTracking(songData, musicPhase) {
        console.log(`🎵 Adding song to tracking - Phase: ${musicPhase}, Song: ${songData.title}`);

        // Ensure music phase summary exists
        if (!this.musicPhaseSummaries[musicPhase]) {
            this.musicPhaseSummaries[musicPhase] = {
                phaseName: this.getMusicPhaseDisplayName(musicPhase),
                songsPlayed: []
            };
        }

        // Check for duplicates in music phase summary (case-insensitive)
        const existsInMusicPhase = this.musicPhaseSummaries[musicPhase].songsPlayed.some(
            song => song.title?.toLowerCase()?.trim() === songData.title?.toLowerCase()?.trim() && song.youtubeId === songData.youtubeId
        );

        // Check for duplicates in current phase data (case-insensitive)
        const existsInCurrentPhase = this.currentPhaseData.songsPlayed.some(
            song => song.title?.toLowerCase()?.trim() === songData.title?.toLowerCase()?.trim() && song.youtubeId === songData.youtubeId
        );

        // Add to music phase summary if not duplicate
        if (!existsInMusicPhase) {
            this.musicPhaseSummaries[musicPhase].songsPlayed.push(songData);
            console.log(`✅ Added to music phase summary: ${musicPhase}`);
        } else {
            console.log(`⚠️ Song already exists in music phase summary: ${musicPhase}`);
        }

        // Add to current phase data if not duplicate (for display purposes)
        if (!existsInCurrentPhase) {
            this.currentPhaseData.songsPlayed.push(songData);
            console.log(`✅ Added to current phase data`);
        } else {
            console.log(`⚠️ Song already exists in current phase data`);
        }

        console.log('🎵 Updated music phase summaries:', this.musicPhaseSummaries);
        console.log('🎮 Updated current phase data:', this.currentPhaseData.songsPlayed);
    }

    attachSkipNextSongListener() {
        const skipNextButton = document.getElementById('skipNextSong');
        if (skipNextButton) {
            skipNextButton.addEventListener('click', () => {
                console.log('Skip next song clicked');
                this.handleSkipNextSong();
            });
        }
    }

    handleSkipNextSong() {
        console.log('Skipping to next song...');

        // Keep the phase summary flag - we want to return to it after song selection
        // this.isShowingPhaseSummary = false; // Removed - keep true to enable refresh

        // Check if we have current genre songs available
        if (this.currentGenreSongs && this.currentGenreSongs.length > 0) {
            // Get songs that haven't been played yet
            const unplayedSongs = this.currentGenreSongs.filter(song =>
                !this.currentPhaseData.songsPlayed.some(played => played.title === song.title)
            );

            if (unplayedSongs.length > 0) {
                console.log('Showing song selection overlay with', unplayedSongs.length, 'unplayed songs');
                // Show song selection overlay directly without changing screens
                this.showSongSelectionOverlay(unplayedSongs);
            } else {
                console.log('No unplayed songs available, returning to music selection');
                // Return to music selection screen if no unplayed songs
                this.showScreen('musicSelectionScreen');
            }
        } else {
            console.log('No current genre songs, returning to music selection');
            // Fallback to music selection screen
            this.showScreen('musicSelectionScreen');
        }
    }

    // Manual skip to evaluation removed - enforcing 3-song minimum requirement

    // Helper function to check if evaluation should be unlocked
    isEvaluationUnlocked() {
        // Simplified logic: evaluation is unlocked if:
        // 1. All required songs have been played (3 unique songs), OR
        // 2. We're in test mode (for easier testing)
        const unlocked = this.allSongsPlayedInPhase || this.testMode;
        console.log(`🔓 Evaluation unlock check: allSongs=${this.allSongsPlayedInPhase}, testMode=${this.testMode} -> ${unlocked}`);
        return unlocked;
    }

    // Helper function to check if voting has been completed
    isVotingCompleted() {
        // Check if voting has been completed for the current music phase
        const currentMusicPhase = this.getCurrentMusicRound();
        const votingCompleted = this.maxiVotingResults && this.maxiVotingResults[currentMusicPhase];
        const evaluationUnlocked = this.isEvaluationUnlocked();

        console.log(`🗳️ Voting completed check: phase=${currentMusicPhase}, evaluation=${evaluationUnlocked}, voting=${!!votingCompleted}`);
        if (this.maxiVotingResults) {
            console.log(`🗳️ Available voting results:`, Object.keys(this.maxiVotingResults));
        }
        return !!votingCompleted;
    }

    attachMaxiVotingEventListeners() {
        // Initialize voting results storage if not exists
        if (!this.maxiVotingResults) {
            this.maxiVotingResults = {};
        }

        // Find all voting buttons
        const voteButtons = document.querySelectorAll('.maxi-vote-btn');
        voteButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                e.preventDefault();
                const songIndex = parseInt(button.dataset.songIndex);
                const musicPhase = button.dataset.musicPhase;
                this.handleMaxiVote(songIndex, musicPhase, button);
            });
        });
    }

    handleMaxiVote(songIndex, musicPhase, clickedButton) {
        // Get the song that was clicked
        const userChoice = this.currentPhaseData.songsPlayed[songIndex];

        // Get Maxi's actual choice
        const musicData = this.musicLibrary[musicPhase];
        let maxiActualChoice = null;

        if (this.selectedGenreData && musicData.options) {
            const selectedGenre = musicData.options.find(option => option.genre === this.selectedGenreData.genre);
            if (selectedGenre && selectedGenre.myChoice) {
                maxiActualChoice = selectedGenre.myChoice.title;
            }
        }

        // Store the voting result
        this.maxiVotingResults[musicPhase] = {
            userChoice: userChoice.title,
            maxiActualChoice: maxiActualChoice,
            timestamp: new Date().toISOString()
        };

        console.log('Maxi voting result:', this.maxiVotingResults[musicPhase]);

        // Add visual feedback - highlight the clicked button
        clickedButton.classList.add('border-purple-500', 'bg-purple-50');

        // Disable all voting buttons
        const allVoteButtons = document.querySelectorAll('.maxi-vote-btn');
        allVoteButtons.forEach(btn => {
            btn.disabled = true;
            btn.classList.add('opacity-50', 'cursor-not-allowed');
        });

        // Show "Revealing Maxi's choice..." message with delay
        const votingSection = document.getElementById('maxiVotingSection');
        if (votingSection) {
            votingSection.innerHTML = `
                <div class="text-center py-6">
                    <div class="text-3xl mb-3">🤔</div>
                    <div class="font-semibold text-gray-800 mb-2">Revealing Maxi's choice...</div>
                    <div class="text-sm text-gray-600">Let's see if you know his taste!</div>
                </div>
            `;

            // After 2 seconds, refresh the voting section to show results
            setTimeout(() => {
                this.refreshVotingSection(musicPhase);
            }, 2000);
        }
    }

    refreshVotingSection(musicPhase) {
        // Regenerate the voting section HTML with results
        const currentMusicPhase = this.getCurrentMusicRound();
        const musicData = this.musicLibrary[currentMusicPhase];

        let maxiChoice = null;
        if (this.selectedGenreData && musicData.options) {
            const selectedGenre = musicData.options.find(option => option.genre === this.selectedGenreData.genre);
            if (selectedGenre && selectedGenre.myChoice) {
                maxiChoice = selectedGenre.myChoice;
            }
        }

        const votingResult = this.maxiVotingResults[musicPhase];
        const resultsHtml = this.createVotingResultsSection(maxiChoice, votingResult);

        // Update the music summary section
        const musicSection = document.querySelector('#phaseCompleteScreen .bg-gradient-to-r.from-purple-50.to-pink-50');
        if (musicSection) {
            // Replace just the voting part while keeping the header and status
            const votingSection = musicSection.querySelector('#maxiVotingSection');
            if (votingSection) {
                votingSection.outerHTML = resultsHtml;
            }
        }

        // CRITICAL FIX: After voting completion, regenerate the entire phase summary
        // to show the Continue button (which only appears after voting is complete)
        console.log('🗳️ Voting completed - regenerating phase summary to show Continue button');
        this.showPhaseSummary();
    }

    updatePhaseSummaryDisplay() {
        console.log('updatePhaseSummaryDisplay called');

        // Only update if we're currently showing the phase summary
        if (!this.isShowingPhaseSummary) {
            return;
        }

        // Update the music progress section
        const musicProgressElement = document.querySelector('#phaseCompleteScreen .bg-gradient-to-r.from-purple-50.to-pink-50');
        if (musicProgressElement) {
            const statusDiv = musicProgressElement.querySelector('div[class*="rounded"]');
            if (statusDiv) {
                // Update the status indicator
                if (this.isVotingCompleted()) {
                    statusDiv.className = 'mt-3 p-2 rounded bg-green-100 text-green-800';
                    statusDiv.textContent = '✅ Evaluation complete - Ready to continue!';
                } else if (this.allSongsPlayedInPhase) {
                    statusDiv.className = 'mt-3 p-2 rounded bg-blue-100 text-blue-800';
                    statusDiv.textContent = '🗳️ Songs complete - Now vote on Maxi\'s choice!';
                } else {
                    statusDiv.className = 'mt-3 p-2 rounded bg-orange-100 text-orange-800';
                    statusDiv.textContent = '⏳ Experience more songs to unlock evaluation';
                }
            }

            // Update songs list
            const songsListElement = musicProgressElement.querySelector('.text-sm.space-y-1');
            if (songsListElement) {
                songsListElement.innerHTML = this.currentPhaseData.songsPlayed.map(song => `
                    <div class="flex justify-between">
                        <span>${song.title}</span>
                        <span class="text-gray-500">${song.genre}</span>
                    </div>
                `).join('');
            }
        }

        // Update the button state
        const nextButton = document.getElementById('nextPhaseButton');
        if (nextButton) {
            if (this.allSongsPlayedInPhase) {
                nextButton.disabled = false;
                nextButton.className = 'px-8 py-3 rounded-xl font-semibold transition-all duration-300 bg-gradient-to-r from-pink-500 to-purple-600 text-white hover:from-pink-600 hover:to-purple-700';
                console.log('Button enabled - all songs played');
            } else {
                nextButton.disabled = true;
                nextButton.className = 'px-8 py-3 rounded-xl font-semibold transition-all duration-300 bg-gray-300 text-gray-500 cursor-not-allowed';
                console.log('Button disabled - need more songs');
            }
        }

        // Update or show the helper text
        let helperText = document.querySelector('#phaseCompleteScreen p.text-sm.text-gray-600');
        if (!this.allSongsPlayedInPhase) {
            if (!helperText) {
                helperText = document.createElement('p');
                helperText.className = 'text-sm text-gray-600 mt-2';
                nextButton.parentNode.appendChild(helperText);
            }
            helperText.textContent = 'Listen to more songs to unlock';
            helperText.style.display = 'block';
        } else if (helperText) {
            helperText.style.display = 'none';
        }
    }

    updateCurrentMusicDisplay(song, isCountdownUpdate = false) {
        // Show display if music is active (even during questions)
        if (!this.isMusicActive) return;

        // Create or update floating music display
        let musicDisplay = document.getElementById('currentMusicDisplay');
        if (!musicDisplay) {
            musicDisplay = document.createElement('div');
            musicDisplay.id = 'currentMusicDisplay';
            musicDisplay.className = 'fixed top-4 right-4 bg-black bg-opacity-75 text-white px-3 py-2 rounded-lg shadow-lg z-50 max-w-80';
            document.body.appendChild(musicDisplay);
        }

        // Get current song if not provided (for countdown updates)
        const currentSong = song || this.getCurrentSong();
        if (!currentSong) return;

        const countdownDisplay = this.timeRemaining > 0 ?
            `<div class="text-xs opacity-75 mt-1 flex items-center justify-between">
                <span>Next song in: ${this.formatTime(this.timeRemaining)}</span>
                <button id="manualMusicTrigger" class="bg-purple-600 hover:bg-purple-700 px-2 py-1 rounded text-xs transition-colors">
                    ⏭️ Skip
                </button>
            </div>` : '';

        musicDisplay.innerHTML = `
            <div class="flex items-center space-x-2">
                <div class="animate-pulse">
                    <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/>
                    </svg>
                </div>
                <div class="text-xs flex-1">
                    <div class="font-semibold truncate">${currentSong.title.split(' - ')[0]}</div>
                    <div class="opacity-75 truncate">${currentSong.title.split(' - ')[1] || ''}</div>
                    <div class="text-xs opacity-60">${this.selectedGenreData?.genre || 'Music'}</div>
                    ${countdownDisplay}
                </div>
            </div>
        `;

        // Add event listener to manual trigger button (remove old ones first)
        const manualTrigger = document.getElementById('manualMusicTrigger');
        if (manualTrigger) {
            // Remove existing listeners by replacing the element
            const newTrigger = manualTrigger.cloneNode(true);
            manualTrigger.parentNode.replaceChild(newTrigger, manualTrigger);

            // Add new listener
            newTrigger.addEventListener('click', (e) => {
                e.stopPropagation();
                this.triggerManualMusicSelection();
            });
        }
    }

    getCurrentSong() {
        // Return the last played song from the stored data
        return this.currentPlayingSong || null;
    }

    hideMusicDisplay() {
        const musicDisplay = document.getElementById('currentMusicDisplay');
        if (musicDisplay) {
            musicDisplay.remove();
        }
    }

    resetGame() {
        // Prevent rapid resets
        if (this._resetting) {
            console.warn('Reset already in progress, ignoring duplicate call');
            return;
        }
        this._resetting = true;

        try {
            this.currentPhase = 0;
            this.currentQuestion = 0;
            this.totalScore = 0;
            this.phaseScores = [0, 0, 0, 0, 0];
            this.userAnswers = [];
            this.musicSelections = [];
            this.currentMusicRound = 0;

            // Reset music system with proper cleanup
            this.stopBackgroundMusic();
            this.clearCountdown();

            // Clear any remaining timers
            if (this.currentSongTimer) {
                clearTimeout(this.currentSongTimer);
                this.currentSongTimer = null;
            }

            this.musicQueue = [];
            this.selectedGenreData = null;
            this.currentPlayingSong = null;
            this.timeRemaining = 0;
            this.removeOverlay();

        // Reset genre selections tracking
        this.selectedGenresByRound = {
            phase1: [],
            phase2: [],
            phase3: [],
            phase4: [],
            phase5: []
        };

        // Reset song tracking
        this.currentGenreSongs = [];
        this.playedSongsFromCurrentGenre = [];

        // Reset phase summaries and music phase summaries
        this.phaseSummaries = [];
        this.musicPhaseSummaries = {};
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [],
            score: 0
        };

        // Reset phase transition tracking
        this.canSwitchGenres = false;
        this.isWaitingForPhaseTransition = false;

            // Reset background
            document.body.className = 'min-h-screen bg-gradient-to-br from-pink-50 to-purple-50';

            this.showScreen('welcomeScreen');
        } finally {
            this._resetting = false;
        }
    }
    
    showScreen(screenId) {
        // Hide all screens
        document.querySelectorAll('.screen').forEach(screen => {
            screen.classList.add('hidden');
        });

        // SPECIAL HANDLING: When showing music selection screen, ensure clean state
        if (screenId === 'musicSelectionScreen') {
            console.log('🧹 Special cleanup for music selection screen');

            // Hide and clear music player container
            const playerContainer = document.getElementById('musicPlayerContainer');
            if (playerContainer) {
                playerContainer.classList.add('hidden');
                playerContainer.innerHTML = '';
                console.log('🎵 Forcefully cleared music player container via showScreen');
            }

            // Remove any stray continue buttons throughout the page
            const continueButtons = document.querySelectorAll('#continueMusicButton');
            continueButtons.forEach(button => {
                console.log('🗑️ Removing stray continue button from DOM via showScreen');
                button.remove();
            });

            // Clear any music timers
            this.clearCountdown();
        }

        // Show the requested screen
        const screen = document.getElementById(screenId);
        if (screen) {
            screen.classList.remove('hidden');

            // Apply cosmic styling for Phase 4
            const gameContainer = document.getElementById('gameContainer');
            if (this.currentPhase === 3 && (screenId === 'phaseIntroScreen' || screenId === 'questionScreen')) {
                // Phase 4 (index 3) gets cosmic treatment
                gameContainer.classList.add('cosmic-background');
                document.body.classList.add('cosmic-background');

                // Make cards cosmic-styled
                const card = screen.querySelector('.bg-white, [class*="rounded"]');
                if (card) {
                    card.classList.add('cosmic-card', 'cosmic-content');
                }
            } else if (screenId === 'resultsScreen') {
                // Final results screen gets cosmic treatment too
                gameContainer.classList.add('cosmic-background');
                document.body.classList.add('cosmic-background');

                const card = screen.querySelector('.bg-white, [class*="rounded"]');
                if (card) {
                    card.classList.add('cosmic-card', 'cosmic-content');
                }
            } else {
                // Remove cosmic styling for other phases
                gameContainer.classList.remove('cosmic-background');
                document.body.classList.remove('cosmic-background');
            }

            // Add animation class
            const card = screen.querySelector('.bg-white, [class*="rounded"]');
            if (card) {
                card.classList.remove('slide-up');
                void card.offsetWidth; // Trigger reflow
                card.classList.add('slide-up');
            }
        }
    }

    // TESTING FRAMEWORK
    // ===================

    enableTestMode() {
        console.log('🧪 TEST MODE ENABLED');
        this.testMode = true;
        this.testResults = [];

        // Add test controls to the page
        this.addTestControls();

        // Override music timing for faster testing
        this.originalMusicDuration = this.musicDuration;
        this.musicDuration = 3000; // 3 seconds instead of 2-3 minutes

        return this;
    }

    addTestControls() {
        const testPanel = document.createElement('div');
        testPanel.id = 'testPanel';
        testPanel.className = 'fixed top-4 right-4 bg-black bg-opacity-80 text-white p-4 rounded-lg z-50 max-w-sm';
        testPanel.innerHTML = `
            <h3 class="font-bold mb-3">🧪 Test Controls</h3>
            <div class="space-y-2 text-sm">
                <button id="runFullTest" class="w-full bg-blue-600 hover:bg-blue-700 px-3 py-1 rounded">
                    Run Full Game Test
                </button>
                <button id="runRealGameTest" class="w-full bg-cyan-600 hover:bg-cyan-700 px-3 py-1 rounded">
                    🎵 Test with Real Songs
                </button>
                <button id="testMusicPhases" class="w-full bg-green-600 hover:bg-green-700 px-3 py-1 rounded">
                    Test All Music Phases
                </button>
                <button id="testCosmicPhase" class="w-full bg-purple-600 hover:bg-purple-700 px-3 py-1 rounded">
                    Test Cosmic Phase
                </button>
                <button id="jumpToPhase" class="w-full bg-yellow-600 hover:bg-yellow-700 px-3 py-1 rounded">
                    Jump to Phase 4
                </button>
                <button id="showTestResults" class="w-full bg-gray-600 hover:bg-gray-700 px-3 py-1 rounded">
                    Show Test Results
                </button>
                <button id="testValidation" class="w-full bg-orange-600 hover:bg-orange-700 px-3 py-1 rounded">
                    🛡️ Test Validation System
                </button>
                <div id="testStatus" class="mt-2 p-2 bg-gray-800 rounded text-xs">
                    Ready for testing...
                </div>
            </div>
        `;

        document.body.appendChild(testPanel);

        // Add event listeners
        document.getElementById('runFullTest').addEventListener('click', () => this.runFullGameTest());
        document.getElementById('runRealGameTest').addEventListener('click', () => this.runRealGameTest());
        document.getElementById('testMusicPhases').addEventListener('click', () => this.testAllMusicPhases());
        document.getElementById('testCosmicPhase').addEventListener('click', () => this.testCosmicPhase());
        document.getElementById('jumpToPhase').addEventListener('click', () => this.jumpToPhase4());
        document.getElementById('showTestResults').addEventListener('click', () => this.showTestResults());
        document.getElementById('testValidation').addEventListener('click', () => this.testValidationSystem());
    }

    updateTestStatus(message) {
        const status = document.getElementById('testStatus');
        if (status) {
            status.textContent = message;
            console.log('🧪 TEST:', message);
        }
    }

    backupGameState() {
        return {
            currentPhase: this.currentPhase,
            currentQuestion: this.currentQuestion,
            totalScore: this.totalScore,
            phaseScores: [...this.phaseScores],
            userAnswers: [...this.userAnswers],
            musicSelections: [...this.musicSelections],
            currentMusicRound: this.currentMusicRound,
            phaseSummaries: [...this.phaseSummaries],
            currentPhaseData: {
                questions: [...this.currentPhaseData.questions],
                answers: [...this.currentPhaseData.answers],
                musicChoices: [...this.currentPhaseData.musicChoices],
                songsPlayed: [...this.currentPhaseData.songsPlayed],
                score: this.currentPhaseData.score
            },
            selectedGenresByRound: {
                phase1: [...this.selectedGenresByRound.phase1],
                phase2: [...this.selectedGenresByRound.phase2],
                phase3: [...this.selectedGenresByRound.phase3],
                phase4: [...this.selectedGenresByRound.phase4],
                phase5: [...this.selectedGenresByRound.phase5]
            }
        };
    }

    restoreGameState(originalState) {
        this.currentPhase = originalState.currentPhase;
        this.currentQuestion = originalState.currentQuestion;
        this.totalScore = originalState.totalScore;
        this.phaseScores = [...originalState.phaseScores];
        this.userAnswers = [...originalState.userAnswers];
        this.musicSelections = [...originalState.musicSelections];
        this.currentMusicRound = originalState.currentMusicRound;
        this.phaseSummaries = [...originalState.phaseSummaries];
        this.currentPhaseData = {
            questions: [...originalState.currentPhaseData.questions],
            answers: [...originalState.currentPhaseData.answers],
            musicChoices: [...originalState.currentPhaseData.musicChoices],
            songsPlayed: [...originalState.currentPhaseData.songsPlayed],
            score: originalState.currentPhaseData.score
        };
        this.selectedGenresByRound = {
            phase1: [...originalState.selectedGenresByRound.phase1],
            phase2: [...originalState.selectedGenresByRound.phase2],
            phase3: [...originalState.selectedGenresByRound.phase3],
            phase4: [...originalState.selectedGenresByRound.phase4],
            phase5: [...originalState.selectedGenresByRound.phase5]
        };

        console.log('🧪 Game state restored to pre-test condition');
    }

    logTestResult(test, passed, details = '') {
        const result = {
            test,
            passed,
            details,
            timestamp: new Date().toISOString()
        };
        this.testResults.push(result);
        console.log(`🧪 ${passed ? '✅' : '❌'} ${test}`, details);
    }

    async runFullGameTest() {
        this.updateTestStatus('Starting full game test...');

        // Backup current game state to restore after test
        const originalState = this.backupGameState();

        // Reset game for testing
        this.resetGame();

        try {
            // Test 1: Game initialization
            this.logTestResult('Game initialization', this.currentPhase === 0 && this.totalScore === 0);

            // Test 2: Start game (shows Phase 1 music selection)
            this.startGame();
            this.logTestResult('Game start shows Phase 1 music', this.currentMusicRound === 1);

            // Test 3: Phase 1 music selection
            await this.simulateMusicSelection('phase1', 1);
            this.logTestResult('Phase 1 music leads to Phase 1 questions', this.currentPhase === 0);

            // Test 4: Complete Phase 1 questions
            await this.simulatePhaseCompletion(0);
            this.logTestResult('Phase 1 questions complete', this.currentPhase === 0);

            // Test 5: Phase 2 music selection (triggered by phase completion)
            await this.simulateMusicSelection('phase2', 2);
            this.logTestResult('Phase 2 music leads to Phase 2 questions', this.currentPhase === 1);

            // Test 6: Complete Phase 2 questions
            await this.simulatePhaseCompletion(1);
            this.logTestResult('Phase 2 questions complete', this.currentPhase === 1);

            // Test 7: Phase 3 music selection
            await this.simulateMusicSelection('phase3', 3);
            this.logTestResult('Phase 3 music leads to Phase 3 questions', this.currentPhase === 2);

            // Test 8: Complete Phase 3 questions
            await this.simulatePhaseCompletion(2);
            this.logTestResult('Phase 3 questions complete', this.currentPhase === 2);

            // Test 9: Phase 4 music selection
            await this.simulateMusicSelection('phase4', 4);
            this.logTestResult('Phase 4 music leads to Phase 4 questions', this.currentPhase === 3);

            // Test 10: Complete Phase 4 questions
            await this.simulatePhaseCompletion(3);
            this.logTestResult('Phase 4 questions complete', this.currentPhase === 3);

            // Test 11: Phase 5 music selection
            await this.simulateMusicSelection('phase5', 5);
            this.logTestResult('Phase 5 music leads to Phase 5 questions', this.currentPhase === 4);

            // Test 12: Complete Phase 5 questions
            await this.simulatePhaseCompletion(4);
            this.logTestResult('Phase 5 questions complete', this.currentPhase === 4);

            // Test 13: Simple completion shown (instead of complex final results)
            this.logTestResult('Simple completion shown', document.getElementById('restartJourney') !== null);

            this.updateTestStatus('Full game test completed!');
            this.showTestResults();

        } catch (error) {
            this.logTestResult('Full game test', false, error.message);
            this.updateTestStatus('Test failed: ' + error.message);
        } finally {
            // Restore original game state
            this.restoreGameState(originalState);
            this.updateTestStatus('Game state restored after testing');
        }
    }

    async runRealGameTest() {
        this.updateTestStatus('Starting REAL game test with song selections...');

        // Enable test mode to reduce song requirements and simplify validation
        this.testMode = true;
        console.log('🧪 Test mode enabled - reduced song requirements to 1 per phase');

        // Backup current game state to restore after test
        const originalState = this.backupGameState();

        // Reset game for testing
        this.resetGame();

        try {
            // Test 1: Game initialization
            this.logTestResult('Game initialization', this.currentPhase === 0 && this.totalScore === 0);

            // Test 2: Start game (should show Phase 1 music selection)
            this.startGame();
            this.logTestResult('Game start shows Phase 1 music', this.currentMusicRound === 1);

            // Test 3: Phase 1 music selection
            await this.simulateRealMusicSelection('phase1', 1, 0);
            this.logTestResult('Phase 1 music leads to Phase 1 questions', this.currentPhase === 0);

            // Test 4: Complete Phase 1 questions
            await this.simulateRealPhaseCompletion(0);
            this.logTestResult('Phase 1 questions complete', this.currentPhase === 0);

            // Note: Phase completion should automatically trigger Phase 2 music selection
            // Test 5: Phase 2 music selection (should already be showing)
            await this.simulateRealMusicSelection('phase2', 2, 1);
            this.logTestResult('Phase 2 music leads to Phase 2 questions', this.currentPhase === 1);

            // Test 6: Complete Phase 2 questions
            await this.simulateRealPhaseCompletion(1);
            this.logTestResult('Phase 2 questions complete', this.currentPhase === 1);

            // Test 7: Phase 3 music selection
            await this.simulateRealMusicSelection('phase3', 3, 2);
            this.logTestResult('Phase 3 music leads to Phase 3 questions', this.currentPhase === 2);

            // Test 8: Complete Phase 3 questions
            await this.simulateRealPhaseCompletion(2);
            this.logTestResult('Phase 3 questions complete', this.currentPhase === 2);

            // Test 9: Phase 4 music selection
            await this.simulateRealMusicSelection('phase4', 4, 0);
            this.logTestResult('Phase 4 music leads to Phase 4 questions', this.currentPhase === 3);

            // Test 10: Complete Phase 4 questions
            await this.simulateRealPhaseCompletion(3);
            this.logTestResult('Phase 4 questions complete', this.currentPhase === 3);

            // Test 11: Phase 5 music selection
            await this.simulateRealMusicSelection('phase5', 5, 0);
            this.logTestResult('Phase 5 music leads to Phase 5 questions', this.currentPhase === 4);

            // Test 12: Complete Phase 5 questions
            await this.simulateRealPhaseCompletion(4);
            this.logTestResult('Phase 5 questions complete', this.currentPhase === 4);

            // Test 13: Verify complete playlist
            const totalSongs = this.getAllSelectedSongs();
            this.logTestResult('Complete playlist generated', totalSongs.length >= 5, `${totalSongs.length} songs selected`);

            // Test 14: Verify playlist has songs from each phase
            const songsByPhase = this.getSongsByPhase();
            this.logTestResult('Songs from all phases', Object.keys(songsByPhase).length >= 5, `Phases: ${Object.keys(songsByPhase).join(', ')}`);

            this.updateTestStatus('REAL game test completed!');
            this.showTestResults();

            // Display playlist results
            this.showPlaylistTestResults(totalSongs, songsByPhase);

        } catch (error) {
            this.logTestResult('Real game test', false, error.message);
            this.updateTestStatus('Test failed: ' + error.message);
        } finally {
            // Disable test mode
            this.testMode = false;

            // Restore original game state
            this.restoreGameState(originalState);
            this.updateTestStatus('Game state restored after testing');
        }
    }

    async waitForAndClickContinueButton() {
        this.updateTestStatus('Waiting for Continue button to appear...');

        // Wait for the Continue button to appear (it's created by createMusicDisplay)
        let continueButton = null;
        let attempts = 0;
        const maxAttempts = 20; // 10 seconds maximum wait

        while (!continueButton && attempts < maxAttempts) {
            continueButton = document.getElementById('continueMusicButton');
            if (!continueButton) {
                await new Promise(resolve => setTimeout(resolve, 500));
                attempts++;
            }
        }

        if (!continueButton) {
            throw new Error('Continue button never appeared after song selection');
        }

        this.updateTestStatus('Continue button found, clicking...');
        console.log('Test: Clicking Continue Journey button');

        // Click the Continue button (this will call continueAfterMusic)
        continueButton.click();

        // Wait a bit for the transition to complete
        await new Promise(resolve => setTimeout(resolve, 1000));
    }

    async simulateRealMusicSelection(round, expectedMusicRound, genreIndex = 0) {
        this.updateTestStatus(`Selecting REAL music for ${round}...`);

        // Validate the round exists
        const musicData = this.musicLibrary[round];
        if (!musicData || !musicData.options) {
            throw new Error(`Music round ${round} not found or has no options`);
        }

        // Validate genre index
        if (genreIndex >= musicData.options.length) {
            genreIndex = 0; // Default to first genre if index is out of bounds
            console.warn(`Genre index ${genreIndex} out of bounds for ${round}, using index 0`);
        }

        // Set the current music round to match the phase being tested
        if (round === 'phase1') this.currentMusicRound = 1;
        else if (round === 'phase2') this.currentMusicRound = 2;
        else if (round === 'phase3') this.currentMusicRound = 3;
        else if (round === 'phase4') this.currentMusicRound = 4;
        else if (round === 'phase5') this.currentMusicRound = 5;

        console.log(`Test: Setting currentMusicRound to ${this.currentMusicRound} for ${round}`);

        // Reset currentPhaseData for clean music phase tracking
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [], // Fresh start for this music phase
            score: 0
        };
        console.log(`Test: Reset currentPhaseData for clean ${round} music tracking`);

        // Show the music selection screen
        this.showMusicSelection(round);

        // Wait for UI to render
        await new Promise(resolve => setTimeout(resolve, 500));

        // Actually select a genre and song
        this.selectMusicGenre(round, genreIndex);

        // Wait for music to start and then simulate song selection
        await new Promise(resolve => setTimeout(resolve, 1000));

        // Find and click the first song that appears (test mode only requires 1 song)
        await this.simulateActualSongSelection();

        // Wait for song to complete (shortened for testing)
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Wait for Continue button to appear and click it (real game flow)
        await this.waitForAndClickContinueButton();

        // Verify the transition worked
        if (round === 'phase5') {
            // For phase5, we expect to reach Phase 5 questions, not final results yet
            const expectedPhase = 4; // Phase 5 questions = currentPhase 4
            this.logTestResult(
                `Music selection ${round}`,
                this.currentPhase === expectedPhase,
                `Expected phase ${expectedPhase}, got ${this.currentPhase}`
            );
        } else {
            // After music, we should have advanced to the next phase
            const expectedPhase = expectedMusicRound - 1; // phase1 music -> phase 0 (index 0), phase2 music -> phase 1, etc.
            this.logTestResult(
                `Music selection ${round}`,
                this.currentPhase === expectedPhase,
                `Expected phase ${expectedPhase}, got ${this.currentPhase}`
            );
        }
    }

    async simulateRealPhaseCompletion(phaseIndex) {
        this.updateTestStatus(`Completing Phase ${phaseIndex + 1} with real answers...`);

        const phase = this.phases[phaseIndex];
        if (!phase) return;

        // Actually go through each question
        for (let i = 0; i < phase.questions.length; i++) {
            this.currentQuestion = i;
            const question = phase.questions[i];

            // Show the question
            this.showQuestion();

            // Wait for UI
            await new Promise(resolve => setTimeout(resolve, 300));

            // Select first answer option
            const answerButtons = document.querySelectorAll('.answer-button');
            if (answerButtons.length > 0) {
                answerButtons[0].click();
            }

            // Wait for answer processing
            await new Promise(resolve => setTimeout(resolve, 500));
        }

        // Complete the phase
        this.showPhaseComplete();
        await new Promise(resolve => setTimeout(resolve, 500));

        // Click next phase button
        const nextButton = document.getElementById('nextPhaseButton');
        if (nextButton) {
            nextButton.click();
        }

        await new Promise(resolve => setTimeout(resolve, 500));
    }

    async simulateActualSongSelection() {
        // Look for song selection overlay or buttons
        const songOptions = document.querySelectorAll('.song-option-overlay, .song-option');

        if (songOptions.length > 0) {
            // Click the first song option
            songOptions[0].click();
            this.updateTestStatus('Song selected from overlay');
        } else {
            // If no overlay, the song might auto-start - that's OK too
            this.updateTestStatus('Song auto-started');
        }
    }

    getCurrentPhaseSongCount() {
        return this.currentPhaseData.songsPlayed ? this.currentPhaseData.songsPlayed.length : 0;
    }

    getAllSelectedSongs() {
        const allSongs = [];

        // Collect from phase summaries
        this.phaseSummaries.forEach(summary => {
            if (summary.songsPlayed) {
                allSongs.push(...summary.songsPlayed);
            }
        });

        // Add current phase songs
        if (this.currentPhaseData.songsPlayed) {
            allSongs.push(...this.currentPhaseData.songsPlayed);
        }

        return allSongs;
    }

    getSongsByPhase() {
        const songsByPhase = {};

        // Use music phase summaries instead of question phase summaries
        Object.entries(this.musicPhaseSummaries).forEach(([musicPhase, summary]) => {
            const phaseName = summary.phaseName || this.getMusicPhaseDisplayName(musicPhase);
            if (summary.songsPlayed && summary.songsPlayed.length > 0) {
                songsByPhase[phaseName] = summary.songsPlayed;
            }
        });

        return songsByPhase;
    }

    showPlaylistTestResults(totalSongs, songsByPhase) {
        // Create inline modal instead of popup window to avoid popup blocker
        const modal = document.createElement('div');
        modal.id = 'playlistTestResults';
        modal.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-xl p-6 max-w-4xl max-h-96 overflow-y-auto m-4">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-2xl font-bold">🎵 Playlist Test Results</h2>
                    <button class="close-modal text-gray-500 hover:text-gray-700 text-2xl">&times;</button>
                </div>

                <div class="space-y-4">
                    <div class="text-lg font-semibold">Total Songs: ${totalSongs.length}</div>

                    <div>
                        <h3 class="text-lg font-bold mb-2">Songs by Phase:</h3>
                        ${Object.entries(songsByPhase).map(([phase, songs]) => `
                            <div class="mb-4 p-3 bg-gray-50 rounded-lg">
                                <h4 class="font-semibold">${phase} (${songs.length} songs)</h4>
                                <ul class="mt-2 space-y-1">
                                    ${songs.map(song => `
                                        <li class="text-sm">
                                            <strong>${song.title}</strong>
                                            ${song.message ? `<br><em class="text-gray-600">${song.message}</em>` : ''}
                                        </li>
                                    `).join('')}
                                </ul>
                            </div>
                        `).join('')}
                    </div>

                    <div>
                        <h3 class="text-lg font-bold mb-2">Complete Playlist:</h3>
                        <div class="bg-gray-50 p-3 rounded-lg">
                            <ol class="space-y-2">
                                ${totalSongs.map((song, index) => `
                                    <li class="text-sm p-2 bg-white rounded">
                                        <span class="font-medium">${index + 1}. ${song.title}</span>
                                        ${song.message ? `<br><em class="text-gray-600">${song.message}</em>` : ''}
                                    </li>
                                `).join('')}
                            </ol>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Add event listener to close modal
        modal.querySelector('.close-modal').addEventListener('click', () => {
            modal.remove();
        });

        // Close modal when clicking outside
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });

        document.body.appendChild(modal);
    }

    async testAllMusicPhases() {
        this.updateTestStatus('Testing all music phases...');

        const musicPhases = ['opening', 'phase1', 'phase2', 'phase3', 'phase4'];

        for (const phase of musicPhases) {
            try {
                const musicData = this.musicLibrary[phase];
                const hasOptions = musicData && musicData.options && musicData.options.length > 0;
                const hasResponse = musicData && musicData.maxiResponse;

                this.logTestResult(`Music phase ${phase} structure`, hasOptions && hasResponse);

                if (hasOptions) {
                    // Test each genre option
                    musicData.options.forEach((option, index) => {
                        const hasGenre = option.genre && option.description;
                        const hasSongs = option.songs && option.songs.length > 0;
                        const hasMyChoice = option.myChoice && option.myChoice.title;

                        this.logTestResult(
                            `${phase} genre ${index} (${option.genre})`,
                            hasGenre && hasSongs && hasMyChoice
                        );
                    });
                }
            } catch (error) {
                this.logTestResult(`Music phase ${phase}`, false, error.message);
            }
        }

        this.updateTestStatus('Music phase testing completed!');
        this.showTestResults();
    }

    async testCosmicPhase() {
        this.updateTestStatus('Testing cosmic phase features...');

        try {
            // Jump to Phase 4
            this.jumpToPhase4();

            // Test cosmic background application
            const gameContainer = document.getElementById('gameContainer');
            this.showScreen('phaseIntroScreen');

            setTimeout(() => {
                const hasCosmicBg = gameContainer.classList.contains('cosmic-background');
                this.logTestResult('Cosmic background styling', hasCosmicBg);

                // Test Phase 4 questions structure
                const phase4 = this.phases[3];
                const hasCosmicQuestions = phase4 && phase4.title === "COSMIC CONNECTION";
                const hasZodiacElements = phase4 && phase4.questions && phase4.questions.some(q =>
                    q.text && (q.text.includes('♓') || q.text.includes('♊') || q.options.some(opt => opt.text && (opt.text.includes('♓') || opt.text.includes('♊'))))
                );

                console.log('🧪 Phase 4 debug:', {
                    phase4Exists: !!phase4,
                    title: phase4?.title,
                    hasQuestions: phase4?.questions?.length,
                    zodiacInText: phase4?.questions?.some(q => q.text?.includes('♓') || q.text?.includes('♊')),
                    zodiacInOptions: phase4?.questions?.some(q => q.options?.some(opt => opt.text?.includes('♓') || opt.text?.includes('♊')))
                });

                this.logTestResult('Phase 4 cosmic questions', hasCosmicQuestions && hasZodiacElements);

                // Test cosmic music library
                const cosmicMusic = this.musicLibrary.phase4;
                const hasCelestialVibes = cosmicMusic && cosmicMusic.title === "Celestial Vibes";
                this.logTestResult('Cosmic music library', hasCelestialVibes);

                this.updateTestStatus('Cosmic phase testing completed!');
                this.showTestResults();
            }, 100);

        } catch (error) {
            this.logTestResult('Cosmic phase test', false, error.message);
            this.updateTestStatus('Cosmic test failed: ' + error.message);
        }
    }

    jumpToPhase4() {
        this.updateTestStatus('Jumping to Phase 4...');

        // Set up game state for Phase 4
        this.currentPhase = 3;
        this.currentQuestion = 0;
        this.currentMusicRound = 4;
        this.totalScore = 75; // Simulate previous scores
        this.phaseScores = [20, 18, 22, 0]; // Mock previous phase scores

        // Mock previous phase summaries
        this.phaseSummaries = [
            { phaseTitle: "Then & Now", phaseEmoji: "🔥", percentage: 80 },
            { phaseTitle: "Growth & Lessons", phaseEmoji: "⚡", percentage: 72 },
            { phaseTitle: "Future & Dreams", phaseEmoji: "🌶️", percentage: 88 }
        ];

        // Start Phase 4
        this.showPhaseIntro();
        this.logTestResult('Jump to Phase 4', this.currentPhase === 3);
    }

    async simulatePhaseCompletion(phaseIndex) {
        this.updateTestStatus(`Simulating Phase ${phaseIndex + 1} completion...`);

        const phase = this.phases[phaseIndex];
        if (!phase) return;

        // Mock answering all questions without affecting real game data
        let mockScore = 0;
        for (let i = 0; i < phase.questions.length; i++) {
            this.currentQuestion = i;
            // Mock selecting first answer option (index 0)
            const question = phase.questions[i];
            const answer = question.options[0];
            mockScore += answer.points;

            // Update only test tracking, not real game data
            this.phaseScores[phaseIndex] += answer.points;
            this.totalScore += answer.points;
        }

        // Mock phase completion without real UI updates
        this.mockPhaseComplete(phaseIndex, mockScore);

        // IMPORTANT: Simulate the nextPhase() call that happens in real game
        // This will trigger the next music selection
        if (phaseIndex < 4) { // Don't call nextPhase for the final phase
            console.log(`🧪 Simulating nextPhase() call after Phase ${phaseIndex + 1} completion`);
            this.mockNextPhase(phaseIndex);
        }

        // Simulate phase transition delay
        await new Promise(resolve => setTimeout(resolve, 100));
    }

    mockPhaseComplete(phaseIndex, score) {
        // Mock phase completion logic without UI changes
        // Just update the necessary tracking for test progression
        this.currentPhaseData.score = score;

        // Mock phase summary data
        const phase = this.phases[phaseIndex];
        const phasePercentage = Math.round((score / 25) * 100);

        // Add mock summary without affecting real game
        this.phaseSummaries.push({
            questions: Array(5).fill('Mock question'),
            answers: Array(5).fill({text: 'Mock answer'}),
            musicChoices: [],
            songsPlayed: [],
            score: score,
            phaseTitle: phase.title,
            phaseEmoji: phase.emoji,
            percentage: phasePercentage
        });
    }

    mockNextPhase(completedPhaseIndex) {
        // Mock the nextPhase() logic to set up correct music round
        console.log(`🧪 Mock nextPhase: completed phase ${completedPhaseIndex}`);

        // Reset phase data for next phase (like real nextPhase)
        this.currentPhaseData = {
            questions: [],
            answers: [],
            musicChoices: [],
            songsPlayed: [],
            score: 0
        };

        // Set up the next music round based on completed phase
        if (completedPhaseIndex === 0) {
            // Phase 1 complete -> Phase 2 music
            this.currentMusicRound = 2;
            console.log(`🧪 Phase 1 complete -> Setting up Phase 2 music (round ${this.currentMusicRound})`);
        } else if (completedPhaseIndex === 1) {
            // Phase 2 complete -> Phase 3 music
            this.currentMusicRound = 3;
            console.log(`🧪 Phase 2 complete -> Setting up Phase 3 music (round ${this.currentMusicRound})`);
        } else if (completedPhaseIndex === 2) {
            // Phase 3 complete -> Phase 4 music
            this.currentMusicRound = 4;
            console.log(`🧪 Phase 3 complete -> Setting up Phase 4 music (round ${this.currentMusicRound})`);
        } else if (completedPhaseIndex === 3) {
            // Phase 4 complete -> Phase 5 music
            this.currentMusicRound = 5;
            console.log(`🧪 Phase 4 complete -> Setting up Phase 5 music (round ${this.currentMusicRound})`);
        }
    }

    async simulateMusicSelection(round, expectedMusicRound) {
        this.updateTestStatus(`Simulating music selection for ${round}...`);

        // Mock music selection without affecting actual game state
        this.mockMusicSelection(round);

        // Wait for simulated music setup
        await new Promise(resolve => setTimeout(resolve, 100));

        // Simulate music completion without real selection
        this.mockContinueAfterMusic();

        // Test that songs were added for validation (which is the real requirement)
        const songsAdded = this.currentPhaseData.songsPlayed && this.currentPhaseData.songsPlayed.length > 0;

        if (round === 'phase5') {
            // For phase5, check if simple completion is shown (no more complex results)
            const completionShown = document.getElementById('restartJourney') !== null;
            this.logTestResult(
                `Music selection ${round}`,
                completionShown || songsAdded // Either completion shown OR songs added
            );
        } else {
            // For other phases, just verify songs were added for validation
            this.logTestResult(
                `Music selection ${round}`,
                songsAdded
            );
        }
    }

    mockMusicSelection(round) {
        // Simulate FULL genre selection with actual song addition for validation
        const musicData = this.musicLibrary[round];
        if (musicData && musicData.options && musicData.options.length > 0) {
            console.log(`🧪 Mock selecting genre for ${round}`);

            // Simulate selecting first genre (index 0)
            const selectedGenre = musicData.options[0];
            this.selectedGenresByRound[round].push(0);

            // Add at least one mock song to satisfy validation
            const mockSong = {
                title: selectedGenre.songs[0]?.title || `Mock Song for ${round}`,
                genre: selectedGenre.genre,
                youtube: selectedGenre.songs[0]?.youtube || 'mock-id',
                youtubeId: selectedGenre.songs[0]?.youtube || 'mock-id',
                timestamp: new Date().toISOString(),
                userSelected: true // Mark as user selected for test
            };

            // Add to both tracking systems
            this.addSongToTracking(mockSong, round);

            console.log(`🧪 Added mock song for validation: ${mockSong.title}`);
        }
    }

    mockContinueAfterMusic() {
        // Simulate the continueAfterMusic logic using the FIXED phase mapping
        console.log(`🧪 Mock continuing after music round ${this.currentMusicRound}`);

        // Use the same mapping as the real continueAfterMusic: musicRound N -> phase N-1
        const targetPhase = this.currentMusicRound - 1;

        if (this.currentMusicRound >= 1 && this.currentMusicRound <= 5) {
            this.currentPhase = targetPhase;
            console.log(`🧪 Mock transition: music round ${this.currentMusicRound} -> phase ${targetPhase}`);

            if (this.currentMusicRound === 5) {
                // Phase 5 music complete, show final results
                this.mockShowFinalResults();
            }
        } else {
            console.error('🧪 Unexpected currentMusicRound in mock:', this.currentMusicRound);
        }
    }

    mockShowFinalResults() {
        // Simulate showing simple completion instead of complex final results
        console.log('🧪 Mock: Showing simple completion for test');
        this.showSimpleCompletion();
    }

    showTestResults() {
        const passed = this.testResults.filter(r => r.passed).length;
        const total = this.testResults.length;

        // Create inline modal instead of popup window to avoid popup blocker
        const modal = document.createElement('div');
        modal.id = 'testResultsModal';
        modal.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50';
        modal.innerHTML = `
            <div class="bg-white rounded-xl p-6 max-w-2xl max-h-96 overflow-y-auto m-4">
                <div class="flex justify-between items-center mb-4">
                    <h2 class="text-2xl font-bold">🧪 Test Results</h2>
                    <button class="close-modal text-gray-500 hover:text-gray-700 text-2xl">&times;</button>
                </div>

                <div class="space-y-4">
                    <div class="text-lg font-semibold ${passed === total ? 'text-green-600' : 'text-red-600'}">
                        Summary: ${passed}/${total} tests passed (${Math.round(passed/total*100)}%)
                    </div>

                    <div class="space-y-2">
                        ${this.testResults.map(r => `
                            <div class="p-3 rounded-lg ${r.passed ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}">
                                <div class="font-semibold ${r.passed ? 'text-green-800' : 'text-red-800'}">
                                    ${r.passed ? '✅' : '❌'} ${r.test}
                                </div>
                                ${r.details ? `<div class="text-sm text-gray-600 mt-1">${r.details}</div>` : ''}
                                <div class="text-xs text-gray-500 mt-1">${r.timestamp}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            </div>
        `;

        // Add event listener to close modal
        modal.querySelector('.close-modal').addEventListener('click', () => {
            modal.remove();
        });

        // Close modal when clicking outside
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });

        document.body.appendChild(modal);
    }

    // Playlist functionality
    savePlaylist() {
        console.log('Save playlist clicked');

        // Show playlist options modal
        this.showPlaylistOptions();
    }

    showPlaylistOptions() {
        // Create modal overlay
        const modal = document.createElement('div');
        modal.id = 'playlistModal';
        modal.className = 'fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50';

        const allSongs = this.getAllSelectedSongs();
        const totalSongs = allSongs.length;

        modal.innerHTML = `
            <div class="bg-white rounded-xl p-6 max-w-md mx-4 slide-up">
                <div class="text-center mb-4">
                    <div class="text-3xl mb-2">🎵</div>
                    <h3 class="text-xl font-bold">Save Our Playlist</h3>
                    <p class="text-sm text-gray-600">${totalSongs} songs from your musical journey</p>
                </div>

                <div class="space-y-3 mb-6">
                    <button id="downloadTxtButton" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-lg transition-all">
                        📄 Download as Text File
                    </button>
                    <button id="copyToClipboardButton" class="w-full bg-green-500 hover:bg-green-600 text-white font-medium py-3 px-4 rounded-lg transition-all">
                        📋 Copy to Clipboard
                    </button>
                    <button id="shareYouTubeButton" class="w-full bg-red-500 hover:bg-red-600 text-white font-medium py-3 px-4 rounded-lg transition-all">
                        🎬 Create YouTube Playlist
                    </button>
                    <button id="exportJSONButton" class="w-full bg-purple-500 hover:bg-purple-600 text-white font-medium py-3 px-4 rounded-lg transition-all">
                        💾 Export as JSON
                    </button>
                </div>

                <button id="closeModalButton" class="w-full bg-gray-300 hover:bg-gray-400 text-gray-700 font-medium py-2 px-4 rounded-lg transition-all">
                    Close
                </button>
            </div>
        `;

        document.body.appendChild(modal);

        // Add event listeners
        document.getElementById('downloadTxtButton')?.addEventListener('click', () => {
            this.downloadPlaylistAsText(allSongs);
        });

        document.getElementById('copyToClipboardButton')?.addEventListener('click', () => {
            this.copyPlaylistToClipboard(allSongs);
        });

        document.getElementById('shareYouTubeButton')?.addEventListener('click', () => {
            this.openYouTubePlaylist(allSongs);
        });

        document.getElementById('exportJSONButton')?.addEventListener('click', () => {
            this.exportPlaylistAsJSON(allSongs);
        });

        document.getElementById('closeModalButton')?.addEventListener('click', () => {
            this.closePlaylistModal();
        });

        // Close on background click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                this.closePlaylistModal();
            }
        });
    }

    getAllSelectedSongs() {
        const allSongs = [];
        const processedSongs = new Set(); // Track unique songs to prevent duplicates

        console.log('Getting all selected songs...');
        console.log('Music phase summaries:', Object.keys(this.musicPhaseSummaries).length);

        // Collect from music phase summaries instead of regular phase summaries
        Object.entries(this.musicPhaseSummaries).forEach(([musicPhase, summary]) => {
            console.log(`Processing music phase: ${musicPhase} (${summary.phaseName})`, 'Songs:', summary.songsPlayed?.length || 0);
            if (summary.songsPlayed) {
                summary.songsPlayed.forEach(song => {
                    // Create unique key for song
                    const songKey = `${song.title}-${song.youtubeId || 'no-id'}`;

                    // Only include songs that were explicitly selected by the user and not already added
                    if (song.userSelected === true && !processedSongs.has(songKey)) {
                        console.log(`Adding USER-SELECTED song from ${summary.phaseName}:`, song.title, 'YouTube ID:', song.youtubeId);
                        allSongs.push({
                            title: song.title,
                            genre: song.genre,
                            phase: summary.phaseName,
                            youtubeId: song.youtubeId,
                            timestamp: song.timestamp
                        });
                        processedSongs.add(songKey);
                    } else if (song.userSelected !== true) {
                        console.log('Skipping NON-USER-SELECTED song:', song.title, 'userSelected:', song.userSelected);
                    } else {
                        console.log('Skipping DUPLICATE song:', song.title);
                    }
                });
            }
        });

        console.log('Total unique selected songs:', allSongs.length);
        console.log('Songs by phase:');
        const songsByPhase = {};
        allSongs.forEach(song => {
            if (!songsByPhase[song.phase]) songsByPhase[song.phase] = [];
            songsByPhase[song.phase].push(song.title);
        });
        console.log(songsByPhase);

        return allSongs;
    }

    downloadPlaylistAsText(songs) {
        const dateStr = new Date().toLocaleDateString();
        const playlistText = `Road Trip Discovery Playlist - ${dateStr}
Generated by claude.ai/code
${'-'.repeat(50)}

Our Musical Journey Together:

${songs.map((song, index) => {
    return `${index + 1}. ${song.title}
   Genre: ${song.genre}
   Phase: ${song.phase}

`;
}).join('')}

${'-'.repeat(50)}
Total Songs: ${songs.length}
Created: ${new Date().toLocaleString()}

Perfect soundtrack for our spa adventure! 🎵💕`;

        // Create and download file
        const blob = new Blob([playlistText], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `road-trip-playlist-${dateStr.replace(/\//g, '-')}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        this.showSuccessMessage('Playlist downloaded as text file!');
        this.closePlaylistModal();
    }

    copyPlaylistToClipboard(songs) {
        const playlistText = `🎵 Our Road Trip Discovery Playlist 🎵

${songs.map((song, index) => `${index + 1}. ${song.title} (${song.genre})`).join('\n')}

Perfect soundtrack for our journey together! 💕`;

        navigator.clipboard.writeText(playlistText).then(() => {
            this.showSuccessMessage('Playlist copied to clipboard!');
            this.closePlaylistModal();
        }).catch(() => {
            // Fallback for older browsers
            const textArea = document.createElement('textarea');
            textArea.value = playlistText;
            document.body.appendChild(textArea);
            textArea.select();
            document.execCommand('copy');
            document.body.removeChild(textArea);
            this.showSuccessMessage('Playlist copied to clipboard!');
            this.closePlaylistModal();
        });
    }

    openYouTubePlaylist(songs) {
        console.log('Creating YouTube playlist for songs:', songs);

        // Filter songs that have YouTube IDs
        const songsWithIds = songs.filter(song => {
            const youtubeId = song.youtubeId || this.findYouTubeId(song.title);
            return youtubeId !== null;
        });

        if (songsWithIds.length === 0) {
            this.showSuccessMessage('No YouTube videos found for your selected songs.');
            this.closePlaylistModal();
            return;
        }

        // Try different YouTube playlist creation methods
        const playlistUrl = this.createYouTubePlaylistUrl(songsWithIds);

        if (playlistUrl) {
            console.log('Opening YouTube playlist URL:', playlistUrl);
            window.open(playlistUrl, '_blank');
            this.showSuccessMessage(`YouTube playlist created with ${songsWithIds.length} songs! 🎵`);
        } else {
            // Fallback: Create search query for all songs
            this.createYouTubeSearchPlaylist(songsWithIds);
        }

        this.closePlaylistModal();
    }

    createYouTubePlaylistUrl(songs) {
        if (songs.length === 0) return null;

        // Get YouTube IDs for all songs
        const youtubeIds = songs.map(song => {
            return song.youtubeId || this.findYouTubeId(song.title);
        }).filter(id => id !== null);

        if (youtubeIds.length === 0) return null;

        console.log('Creating playlist with YouTube IDs:', youtubeIds);

        // Method 1: Try watch_videos URL (creates a temporary queue)
        if (youtubeIds.length > 1) {
            const playlistUrl = `https://www.youtube.com/watch_videos?video_ids=${youtubeIds.join(',')}`;
            console.log('Generated playlist URL:', playlistUrl);
            return playlistUrl;
        }

        // Method 2: If only one song, create a playlist starting from that song
        if (youtubeIds.length === 1) {
            // Use the first video and let YouTube suggest similar songs
            const singleVideoUrl = `https://www.youtube.com/watch?v=${youtubeIds[0]}&list=RD${youtubeIds[0]}`;
            console.log('Generated single video with radio URL:', singleVideoUrl);
            return singleVideoUrl;
        }

        return null;
    }

    openIndividualYouTubeVideos(songs) {
        console.log('Fallback: Opening individual YouTube videos');

        songs.forEach((song, index) => {
            const youtubeId = song.youtubeId || this.findYouTubeId(song.title);

            if (youtubeId) {
                setTimeout(() => {
                    const url = `https://www.youtube.com/watch?v=${youtubeId}`;
                    console.log('Opening URL:', url);
                    window.open(url, '_blank');
                }, index * 500); // Stagger the opening
            }
        });

        this.showSuccessMessage(`Opened ${songs.length} individual videos on YouTube!`);
    }

    createYouTubeSearchPlaylist(songs) {
        console.log('Creating YouTube search playlist as fallback');

        // Create a search query with all song titles
        const searchTerms = songs.map(song => `"${song.title}"`).join(' OR ');
        const encodedSearch = encodeURIComponent(searchTerms);
        const searchUrl = `https://www.youtube.com/results?search_query=${encodedSearch}`;

        console.log('Opening YouTube search URL:', searchUrl);
        window.open(searchUrl, '_blank');

        this.showSuccessMessage(`Created YouTube search for all ${songs.length} songs! You can manually add them to a playlist.`);
    }

    findYouTubeId(songTitle) {
        console.log('Looking for YouTube ID for song:', songTitle);

        // Search through the music library to find the YouTube ID
        const allGenres = Object.values(this.musicLibrary);
        for (const round of allGenres) {
            for (const option of round.options) {
                // Check regular songs
                for (const song of option.songs) {
                    console.log('Checking against library song:', song.title);
                    if (song.title === songTitle) {
                        console.log('Found match! YouTube ID:', song.youtube);
                        return song.youtube;
                    }
                }
                // Check myChoice song
                if (option.myChoice && option.myChoice.title === songTitle) {
                    console.log('Found match in myChoice! YouTube ID:', option.myChoice.youtube);
                    return option.myChoice.youtube;
                }
            }
        }
        console.log('No YouTube ID found for:', songTitle);
        return null;
    }

    exportPlaylistAsJSON(songs) {
        const playlistData = {
            title: "Road Trip Discovery Playlist",
            createdDate: new Date().toISOString(),
            totalSongs: songs.length,
            gameScore: `${this.totalScore}/${this.maxScore} (${Math.round((this.totalScore / this.maxScore) * 100)}%)`,
            songs: songs.map(song => ({
                title: song.title,
                genre: song.genre,
                phase: song.phase,
                youtubeId: song.youtubeId || this.findYouTubeId(song.title), // Use stored ID or fallback
                timestamp: song.timestamp
            })),
            phases: this.phaseSummaries.map(summary => ({
                title: summary.phaseTitle,
                emoji: summary.phaseEmoji,
                score: summary.percentage,
                songsCount: summary.songsPlayed.length
            }))
        };

        const blob = new Blob([JSON.stringify(playlistData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `road-trip-playlist-${new Date().toISOString().split('T')[0]}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        this.showSuccessMessage('Playlist exported as JSON file!');
        this.closePlaylistModal();
    }

    showSuccessMessage(message) {
        const notification = document.createElement('div');
        notification.className = 'fixed top-4 left-1/2 transform -translate-x-1/2 bg-green-500 text-white px-6 py-3 rounded-lg shadow-lg z-50';
        notification.innerHTML = `✅ ${message}`;

        document.body.appendChild(notification);

        setTimeout(() => {
            notification.remove();
        }, 3000);
    }

    closePlaylistModal() {
        const modal = document.getElementById('playlistModal');
        if (modal) {
            modal.remove();
        }
    }

    async testValidationSystem() {
        this.updateTestStatus('Testing validation and bypass prevention...');

        // Backup current state
        const originalState = this.backupGameState();

        try {
            // Test 1: Validation prevents continueAfterMusic without songs
            this.resetGame();
            this.currentMusicRound = 2; // Simulate Phase 2 music
            this.currentPhaseData.songsPlayed = []; // No songs

            let validationWorked = false;
            try {
                this.continueAfterMusic();
                validationWorked = false; // Should not reach here
            } catch (error) {
                validationWorked = true; // Validation should prevent this
            }

            this.logTestResult('Validation prevents bypass without songs', validationWorked);

            // Test 2: Test bypass detection
            this.showScreen('musicSelectionScreen');
            const playerContainer = document.getElementById('musicPlayerContainer');
            if (playerContainer) {
                playerContainer.classList.add('hidden');
                playerContainer.innerHTML = '<button id="continueMusicButton">Continue Journey</button>';
            }

            // Try to call continueAfterMusic in invalid state
            validationWorked = false;
            try {
                this.continueAfterMusic();
                validationWorked = false;
            } catch (error) {
                validationWorked = true;
            }

            this.logTestResult('Bypass detection works', validationWorked);

            // Test 3: Button cleanup works
            this.showMusicSelection('phase2');
            const continueButtons = document.querySelectorAll('#continueMusicButton');
            this.logTestResult('Continue buttons cleaned up', continueButtons.length === 0);

            // Test 4: Test mode reduces requirements
            this.testMode = true;
            this.currentPhaseData.songsPlayed = [{title: 'Test Song', userSelected: true}];
            const evaluation1 = this.isEvaluationUnlocked();

            this.testMode = false;
            const evaluation2 = this.isEvaluationUnlocked();

            this.logTestResult('Test mode reduces song requirements', evaluation1 && !evaluation2);

            this.updateTestStatus('Validation system test completed!');
            this.showTestResults();

        } catch (error) {
            this.logTestResult('Validation system test', false, error.message);
            this.updateTestStatus('Validation test failed: ' + error.message);
        } finally {
            // Restore original state
            this.restoreGameState(originalState);
            this.updateTestStatus('Game state restored after validation testing');
        }
    }

    getAllYouTubeVideos() {
        console.log('🎵 Extracting all YouTube videos from music library...');

        const allVideos = [];
        const duplicateIds = new Set();
        const mismatches = [];

        // Process each music phase
        Object.entries(this.musicLibrary).forEach(([phaseName, phaseData]) => {
            console.log(`\n📀 Processing ${phaseName.toUpperCase()}:`);
            console.log(`Title: "${phaseData.title}"`);

            if (phaseData.options) {
                phaseData.options.forEach((genre, genreIndex) => {
                    console.log(`\n  🎭 Genre ${genreIndex}: ${genre.genre}`);

                    // Process regular songs
                    if (genre.songs) {
                        genre.songs.forEach((song, songIndex) => {
                            const videoData = {
                                phase: phaseName,
                                phaseTitle: phaseData.title,
                                genre: genre.genre,
                                genreIndex: genreIndex,
                                songIndex: songIndex,
                                title: song.title,
                                youtubeId: song.youtube,
                                year: song.year,
                                url: `https://www.youtube.com/watch?v=${song.youtube}`,
                                type: 'regular'
                            };

                            allVideos.push(videoData);

                            // Check for duplicate IDs
                            if (duplicateIds.has(song.youtube)) {
                                console.warn(`⚠️ Duplicate YouTube ID found: ${song.youtube} for "${song.title}"`);
                            } else {
                                duplicateIds.add(song.youtube);
                            }

                            console.log(`    ${songIndex + 1}. ${song.title} (${song.year}) - ${song.youtube}`);
                        });
                    }

                    // Process myChoice song
                    if (genre.myChoice) {
                        const song = genre.myChoice;
                        const videoData = {
                            phase: phaseName,
                            phaseTitle: phaseData.title,
                            genre: genre.genre,
                            genreIndex: genreIndex,
                            songIndex: 'myChoice',
                            title: song.title,
                            youtubeId: song.youtube,
                            year: song.year,
                            url: `https://www.youtube.com/watch?v=${song.youtube}`,
                            message: song.message,
                            type: 'myChoice'
                        };

                        allVideos.push(videoData);

                        if (duplicateIds.has(song.youtube)) {
                            console.warn(`⚠️ Duplicate YouTube ID found: ${song.youtube} for "${song.title}" (myChoice)`);
                        } else {
                            duplicateIds.add(song.youtube);
                        }

                        console.log(`    💝 myChoice: ${song.title} (${song.year}) - ${song.youtube}`);
                        if (song.message) {
                            console.log(`       Message: "${song.message}"`);
                        }
                    }
                });
            }
        });

        // Display summary
        console.log(`\n📊 SUMMARY:`);
        console.log(`Total videos: ${allVideos.length}`);
        console.log(`Unique YouTube IDs: ${duplicateIds.size}`);
        console.log(`Phases: ${Object.keys(this.musicLibrary).length}`);

        // Group by phase for better overview
        const byPhase = {};
        allVideos.forEach(video => {
            if (!byPhase[video.phase]) byPhase[video.phase] = [];
            byPhase[video.phase].push(video);
        });

        console.log('\n📋 VIDEOS BY PHASE:');
        Object.entries(byPhase).forEach(([phase, videos]) => {
            console.log(`\n${phase.toUpperCase()} (${videos.length} videos):`);
            videos.forEach(video => {
                console.log(`  • ${video.title} | ${video.youtubeId} | ${video.genre}`);
            });
        });

        // Create downloadable report
        this.createYouTubeReport(allVideos);

        return allVideos;
    }

    // Video verification tool to identify potential title/content mismatches
    analyzeVideoMismatches() {
        console.log('🔍 Starting comprehensive video mismatch analysis...');

        const allVideos = this.getAllYouTubeVideos();
        const analysisReport = {
            totalVideos: allVideos.length,
            suspiciousVideos: [],
            duplicateIds: [],
            shortIds: [],
            genericTitles: [],
            genreMismatches: [],
            recommendations: [],
            generatedAt: new Date().toISOString()
        };

        // Track YouTube IDs to find duplicates
        const idTracker = {};

        allVideos.forEach(video => {
            const { youtubeId, title, genre, phase } = video;

            // Track duplicate YouTube IDs
            if (idTracker[youtubeId]) {
                idTracker[youtubeId].push(video);
            } else {
                idTracker[youtubeId] = [video];
            }

            // Check for suspiciously short YouTube IDs (should be 11 characters)
            if (youtubeId.length !== 11) {
                analysisReport.shortIds.push({
                    ...video,
                    issue: `Invalid YouTube ID length: ${youtubeId.length} characters (should be 11)`,
                    severity: 'high'
                });
            }

            // Check for generic/placeholder titles
            const genericPatterns = [
                /^song\s*\d*$/i,
                /^track\s*\d*$/i,
                /^music\s*\d*$/i,
                /^untitled/i,
                /^test/i,
                /^placeholder/i,
                /^sample/i,
                /^temp/i,
                /^demo/i
            ];

            const isGeneric = genericPatterns.some(pattern => pattern.test(title));
            if (isGeneric) {
                analysisReport.genericTitles.push({
                    ...video,
                    issue: 'Generic or placeholder title detected',
                    severity: 'medium'
                });
            }

            // Advanced genre-title mismatch detection
            const titleLower = title.toLowerCase();
            const genreLower = genre ? genre.toLowerCase() : '';

            // Classical music indicators
            const classicalKeywords = ['symphony', 'concerto', 'sonata', 'bach', 'mozart', 'beethoven', 'chopin', 'classical', 'orchestra', 'philharmonic'];
            const hasClassicalKeywords = classicalKeywords.some(keyword => titleLower.includes(keyword));

            // Electronic/EDM indicators
            const electronicKeywords = ['remix', 'edm', 'electronic', 'dubstep', 'techno', 'house', 'trance', 'electro', 'digital', 'synthesized'];
            const hasElectronicKeywords = electronicKeywords.some(keyword => titleLower.includes(keyword));

            // Hip-hop/Rap indicators
            const hiphopKeywords = ['rap', 'hip hop', 'hiphop', 'freestyle', 'mc ', 'dj ', 'beats', 'rhyme'];
            const hasHiphopKeywords = hiphopKeywords.some(keyword => titleLower.includes(keyword));

            // Rock/Metal indicators
            const rockKeywords = ['rock', 'metal', 'punk', 'grunge', 'alternative', 'indie', 'guitar', 'bass', 'drums'];
            const hasRockKeywords = rockKeywords.some(keyword => titleLower.includes(keyword));

            // Jazz indicators
            const jazzKeywords = ['jazz', 'blues', 'swing', 'bebop', 'improvisation', 'saxophone', 'trumpet'];
            const hasJazzKeywords = jazzKeywords.some(keyword => titleLower.includes(keyword));

            // Check for mismatches
            if (hasClassicalKeywords && !genreLower.includes('classical') && !genreLower.includes('orchestral')) {
                analysisReport.genreMismatches.push({
                    ...video,
                    issue: 'Classical music indicators in non-classical genre',
                    detected: 'Classical keywords',
                    confidence: 'high',
                    severity: 'high'
                });
            }

            if (hasElectronicKeywords && (genreLower.includes('acoustic') || genreLower.includes('folk') || genreLower.includes('country') || genreLower.includes('classical'))) {
                analysisReport.genreMismatches.push({
                    ...video,
                    issue: 'Electronic music indicators in acoustic/traditional genre',
                    detected: 'Electronic keywords',
                    confidence: 'medium',
                    severity: 'medium'
                });
            }

            if (hasHiphopKeywords && (genreLower.includes('classical') || genreLower.includes('folk') || genreLower.includes('country') || genreLower.includes('jazz'))) {
                analysisReport.genreMismatches.push({
                    ...video,
                    issue: 'Hip-hop/Rap indicators in inappropriate genre',
                    detected: 'Hip-hop keywords',
                    confidence: 'high',
                    severity: 'high'
                });
            }

            if (hasRockKeywords && (genreLower.includes('classical') || genreLower.includes('jazz') || genreLower.includes('ambient'))) {
                analysisReport.genreMismatches.push({
                    ...video,
                    issue: 'Rock music indicators in incompatible genre',
                    detected: 'Rock keywords',
                    confidence: 'medium',
                    severity: 'medium'
                });
            }

            if (hasJazzKeywords && (genreLower.includes('electronic') || genreLower.includes('metal') || genreLower.includes('punk'))) {
                analysisReport.genreMismatches.push({
                    ...video,
                    issue: 'Jazz indicators in incompatible genre',
                    detected: 'Jazz keywords',
                    confidence: 'medium',
                    severity: 'medium'
                });
            }
        });

        // Process duplicates
        Object.entries(idTracker).forEach(([youtubeId, videos]) => {
            if (videos.length > 1) {
                analysisReport.duplicateIds.push({
                    youtubeId,
                    count: videos.length,
                    videos: videos.map(v => ({
                        title: v.title,
                        phase: v.phase,
                        genre: v.genre,
                        fullPath: `${v.phase} > ${v.genre} > ${v.title}`
                    })),
                    severity: 'medium'
                });
            }
        });

        // Compile all suspicious videos
        analysisReport.suspiciousVideos = [
            ...analysisReport.shortIds,
            ...analysisReport.genericTitles,
            ...analysisReport.genreMismatches
        ];

        // Generate recommendations
        if (analysisReport.duplicateIds.length > 0) {
            analysisReport.recommendations.push('🔄 Review duplicate YouTube IDs - same video used multiple times may confuse users');
        }
        if (analysisReport.shortIds.length > 0) {
            analysisReport.recommendations.push('⚠️ Fix invalid YouTube IDs (must be exactly 11 characters)');
        }
        if (analysisReport.genericTitles.length > 0) {
            analysisReport.recommendations.push('📝 Replace generic titles with actual song names and artists');
        }
        if (analysisReport.genreMismatches.length > 0) {
            analysisReport.recommendations.push('🎵 Review genre-title mismatches - content may not match expected genre');
        }

        // Log comprehensive results
        console.log('\n🔍 VIDEO MISMATCH ANALYSIS COMPLETE');
        console.log('📊 SUMMARY:', {
            totalVideos: analysisReport.totalVideos,
            totalIssues: analysisReport.suspiciousVideos.length + analysisReport.duplicateIds.length,
            duplicateIds: analysisReport.duplicateIds.length,
            invalidIds: analysisReport.shortIds.length,
            genericTitles: analysisReport.genericTitles.length,
            genreMismatches: analysisReport.genreMismatches.length
        });

        if (analysisReport.suspiciousVideos.length > 0) {
            console.log('\n⚠️ SUSPICIOUS VIDEOS FOUND:');
            analysisReport.suspiciousVideos.forEach((video, index) => {
                console.log(`${index + 1}. [${video.severity.toUpperCase()}] ${video.phase} > ${video.genre} > ${video.title}`);
                console.log(`   Issue: ${video.issue}`);
                console.log(`   YouTube: https://www.youtube.com/watch?v=${video.youtubeId}`);
            });
        }

        if (analysisReport.duplicateIds.length > 0) {
            console.log('\n🔄 DUPLICATE YOUTUBE IDS:');
            analysisReport.duplicateIds.forEach((dup, index) => {
                console.log(`${index + 1}. ID: ${dup.youtubeId} (used ${dup.count} times)`);
                dup.videos.forEach(v => console.log(`   - ${v.fullPath}`));
            });
        }

        console.log('\n💡 RECOMMENDATIONS:');
        analysisReport.recommendations.forEach((rec, index) => {
            console.log(`${index + 1}. ${rec}`);
        });

        return analysisReport;
    }

    // Generate comprehensive mismatch report for download
    generateMismatchReport() {
        console.log('📄 Generating comprehensive video mismatch report...');

        const analysis = this.analyzeVideoMismatches();

        const reportContent = `ROAD TRIP MUSIC GAME - VIDEO MISMATCH ANALYSIS REPORT
${'='.repeat(80)}
Generated: ${analysis.generatedAt}
Total Videos Analyzed: ${analysis.totalVideos}

EXECUTIVE SUMMARY
${'-'.repeat(40)}
Total Issues Found: ${analysis.suspiciousVideos.length + analysis.duplicateIds.length}
• Invalid YouTube IDs: ${analysis.shortIds.length}
• Generic/Placeholder Titles: ${analysis.genericTitles.length}
• Genre-Title Mismatches: ${analysis.genreMismatches.length}
• Duplicate YouTube IDs: ${analysis.duplicateIds.length}

PRIORITY ISSUES (High Severity)
${'-'.repeat(40)}
${analysis.suspiciousVideos
    .filter(v => v.severity === 'high')
    .map((video, index) =>
        `${index + 1}. ${video.phase} > ${video.genre} > ${video.title}
   Issue: ${video.issue}
   YouTube: https://www.youtube.com/watch?v=${video.youtubeId}
   Severity: HIGH`
    ).join('\n\n')}

MEDIUM PRIORITY ISSUES
${'-'.repeat(40)}
${analysis.suspiciousVideos
    .filter(v => v.severity === 'medium')
    .map((video, index) =>
        `${index + 1}. ${video.phase} > ${video.genre} > ${video.title}
   Issue: ${video.issue}
   YouTube: https://www.youtube.com/watch?v=${video.youtubeId}
   Severity: MEDIUM`
    ).join('\n\n')}

DUPLICATE YOUTUBE IDS
${'-'.repeat(40)}
${analysis.duplicateIds.map((dup, index) =>
    `${index + 1}. YouTube ID: ${dup.youtubeId} (used ${dup.count} times)
   URL: https://www.youtube.com/watch?v=${dup.youtubeId}
   Used in:
${dup.videos.map(v => `   • ${v.fullPath}`).join('\n')}`
).join('\n\n')}

INVALID YOUTUBE IDS
${'-'.repeat(40)}
${analysis.shortIds.map((vid, index) =>
    `${index + 1}. ${vid.phase} > ${vid.genre} > ${vid.title}
   Current ID: "${vid.youtubeId}" (${vid.youtubeId.length} characters)
   Expected: 11 characters
   Issue: ${vid.issue}`
).join('\n\n')}

GENERIC/PLACEHOLDER TITLES
${'-'.repeat(40)}
${analysis.genericTitles.map((vid, index) =>
    `${index + 1}. ${vid.phase} > ${vid.genre} > "${vid.title}"
   YouTube: https://www.youtube.com/watch?v=${vid.youtubeId}
   Recommendation: Replace with actual song title`
).join('\n\n')}

GENRE-TITLE MISMATCHES
${'-'.repeat(40)}
${analysis.genreMismatches.map((vid, index) =>
    `${index + 1}. ${vid.phase} > ${vid.genre} > ${vid.title}
   Issue: ${vid.issue}
   Detected: ${vid.detected}
   Confidence: ${vid.confidence}
   YouTube: https://www.youtube.com/watch?v=${vid.youtubeId}
   Recommendation: Verify video content matches genre expectations`
).join('\n\n')}

ACTION ITEMS & RECOMMENDATIONS
${'-'.repeat(40)}
${analysis.recommendations.map((rec, index) => `${index + 1}. ${rec}`).join('\n')}

NEXT STEPS
${'-'.repeat(40)}
1. Review high-severity issues first (invalid IDs, major genre mismatches)
2. Verify suspicious videos by watching them or checking titles
3. Replace generic titles with proper song names
4. Consider consolidating duplicate videos or using different versions
5. Update music library data with corrected information

TECHNICAL NOTES
${'-'.repeat(40)}
• YouTube IDs must be exactly 11 characters (alphanumeric plus - and _)
• Genre detection uses keyword matching - may have false positives
• Manual verification recommended for flagged videos
• Report generated automatically by game analysis system

${'='.repeat(80)}
End of Report
`;

        // Create and download the report
        const blob = new Blob([reportContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `video-mismatch-analysis-${new Date().toISOString().split('T')[0]}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log('📄 Comprehensive mismatch analysis report downloaded successfully!');
        console.log('📋 Analysis object returned for further inspection');

        return analysis;
    }

    createYouTubeReport(allVideos) {
        const report = `ROAD TRIP MUSIC GAME - YOUTUBE VIDEOS REPORT
Generated: ${new Date().toLocaleString()}
Total Videos: ${allVideos.length}

${'='.repeat(80)}

DETAILED LISTING:

${allVideos.map(video => `
Phase: ${video.phase.toUpperCase()} - ${video.phaseTitle}
Genre: ${video.genre} (Index: ${video.genreIndex})
Song: ${video.title} (${video.year})
YouTube ID: ${video.youtubeId}
URL: ${video.url}
Type: ${video.type}${video.message ? `\nMessage: "${video.message}"` : ''}
${'-'.repeat(40)}`).join('')}

${'='.repeat(80)}

QUICK REFERENCE (CSV FORMAT):

Phase,Genre,Title,Year,YouTube_ID,URL,Type,Message
${allVideos.map(video =>
    `${video.phase},"${video.genre}","${video.title}",${video.year},${video.youtubeId},${video.url},${video.type},"${video.message || ''}"`
).join('\n')}

${'='.repeat(80)}

YOUTUBE URLS ONLY:

${allVideos.map(video => video.url).join('\n')}
`;

        // Create and download the report
        const blob = new Blob([report], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `youtube-videos-report-${new Date().toISOString().split('T')[0]}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log('📄 Report downloaded as text file!');
    }
}

// Initialize game when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const game = new RoadTripMusicGame();
    window.roadTripGame = game; // Make it globally accessible for debugging

    // Add global test functions for easy console access
    window.enableTestMode = () => game.enableTestMode();
    window.runFullGameTest = () => game.runFullGameTest();
    window.runRealGameTest = () => game.runRealGameTest();
    window.testMusicPhases = () => game.testAllMusicPhases();
    window.testCosmicPhase = () => game.testCosmicPhase();
    window.jumpToPhase4 = () => game.jumpToPhase4();
    window.testValidation = () => game.testValidationSystem();
    window.getAllYouTubeVideos = () => game.getAllYouTubeVideos();
    window.analyzeVideoMismatches = () => game.analyzeVideoMismatches();
    window.generateMismatchReport = () => game.generateMismatchReport();
    window.checkAllVideoLinks = () => game.checkAllVideoLinks();
    window.generateLinkStatusReport = () => game.generateLinkStatusReport();
    window.quickCheckVideo = (youtubeId) => game.quickCheckVideo(youtubeId);
    window.showVideoTable = () => game.showVideoTable();
    window.checkVideoLinks = () => game.checkVideoLinks();
    window.debugGameState = () => {
        console.log('🎮 Current Game State:', {
            currentPhase: game.currentPhase,
            currentQuestion: game.currentQuestion,
            currentMusicRound: game.currentMusicRound,
            totalScore: game.totalScore,
            phaseScores: game.phaseScores,
            songsInCurrentPhase: game.currentPhaseData.songsPlayed.length,
            phaseSummaries: game.phaseSummaries.length
        });
    };

    // Console instructions
    console.log(`
🧪 ROAD TRIP GAME TESTING FRAMEWORK
=====================================

Available console commands:
• enableTestMode()     - Enable visual test controls
• runFullGameTest()    - Test complete game flow (mock)
• runRealGameTest()    - 🎵 Test with REAL song selections
• testMusicPhases()    - Test all music libraries
• testCosmicPhase()    - Test Phase 4 cosmic features
• jumpToPhase4()       - Jump directly to cosmic phase
• testValidation()     - 🛡️ Test validation and bypass prevention
• getAllYouTubeVideos() - 📋 Extract all YouTube videos & download report
• analyzeVideoMismatches() - 🔍 Analyze videos for title/genre mismatches
• generateMismatchReport() - 📄 Generate comprehensive mismatch analysis report
• checkAllVideoLinks() - 🔗 Check availability of all 84 YouTube videos
• generateLinkStatusReport() - 📊 Generate comprehensive link status report
• quickCheckVideo('youtubeId') - ⚡ Quick check single video by ID
• debugGameState()     - Show current game state

Game object available as: window.roadTripGame
    `);
});