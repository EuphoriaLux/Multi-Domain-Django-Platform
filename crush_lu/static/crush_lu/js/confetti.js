/**
 * Lightweight canvas-based confetti animation for Crush.lu
 * Triggers on profile approval, event registration, and mutual connections.
 *
 * Usage:
 *   CrushConfetti.fire()              — default burst
 *   CrushConfetti.fire({ count: 80 }) — custom particle count
 */
(function () {
    'use strict';

    var DEFAULTS = {
        count: 60,
        gravity: 0.6,
        drag: 0.02,
        spread: 90,
        durationMs: 3000,
        colors: ['#9B59B6', '#FF6B9D', '#FFD700', '#6DD5FA', '#FC5C7D', '#E040FB']
    };

    function randomRange(min, max) {
        return Math.random() * (max - min) + min;
    }

    function createCanvas() {
        var canvas = document.createElement('canvas');
        canvas.style.cssText =
            'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999';
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        document.body.appendChild(canvas);
        return canvas;
    }

    function Particle(x, y, opts) {
        var angle = randomRange(0, Math.PI * 2);
        var speed = randomRange(4, 12);
        this.x = x;
        this.y = y;
        this.vx = Math.cos(angle) * speed;
        this.vy = Math.sin(angle) * speed - randomRange(2, 6);
        this.size = randomRange(4, 8);
        this.color = opts.colors[Math.floor(Math.random() * opts.colors.length)];
        this.rotation = randomRange(0, Math.PI * 2);
        this.rotationSpeed = randomRange(-0.1, 0.1);
        this.opacity = 1;
        this.gravity = opts.gravity;
        this.drag = opts.drag;
        // 0 = square, 1 = circle
        this.shape = Math.random() > 0.5 ? 1 : 0;
    }

    Particle.prototype.update = function () {
        this.vx *= 1 - this.drag;
        this.vy *= 1 - this.drag;
        this.vy += this.gravity;
        this.x += this.vx;
        this.y += this.vy;
        this.rotation += this.rotationSpeed;
    };

    Particle.prototype.draw = function (ctx) {
        ctx.save();
        ctx.translate(this.x, this.y);
        ctx.rotate(this.rotation);
        ctx.globalAlpha = this.opacity;
        ctx.fillStyle = this.color;
        if (this.shape === 1) {
            ctx.beginPath();
            ctx.arc(0, 0, this.size / 2, 0, Math.PI * 2);
            ctx.fill();
        } else {
            ctx.fillRect(-this.size / 2, -this.size / 2, this.size, this.size);
        }
        ctx.restore();
    };

    function fire(userOpts) {
        var opts = {};
        var key;
        for (key in DEFAULTS) {
            opts[key] = DEFAULTS[key];
        }
        if (userOpts) {
            for (key in userOpts) {
                opts[key] = userOpts[key];
            }
        }

        var canvas = createCanvas();
        var ctx = canvas.getContext('2d');
        var cx = canvas.width / 2;
        var cy = canvas.height * 0.35;

        var particles = [];
        for (var i = 0; i < opts.count; i++) {
            particles.push(new Particle(cx, cy, opts));
        }

        var start = performance.now();
        var duration = opts.durationMs;

        function loop(now) {
            var elapsed = now - start;
            var progress = Math.min(elapsed / duration, 1);

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            for (var i = 0; i < particles.length; i++) {
                var p = particles[i];
                p.update();
                // Fade out in the last 30% of the animation
                if (progress > 0.7) {
                    p.opacity = Math.max(0, 1 - (progress - 0.7) / 0.3);
                }
                p.draw(ctx);
            }

            if (progress < 1) {
                requestAnimationFrame(loop);
            } else {
                canvas.remove();
            }
        }

        requestAnimationFrame(loop);
    }

    // Expose globally
    window.CrushConfetti = { fire: fire };
})();
