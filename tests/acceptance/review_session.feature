Feature: Review session

    The session runner is client-side: the page ships an empty shell and
    lute-review.js fills it from POST /review/start, then posts each
    grade.  A card that never renders looks exactly like a session with
    nothing due, so these scenarios read the live DOM rather than the
    markup.

    Keywords are explicit here because pytest-bdd matches a step only
    under the keyword it was registered with, and `I visit` is a Given
    throughout this suite.


    Background:
        Given a running site
        And demo languages


    Scenario: A learning term is auto-admitted, graded, and ungraded
        Given a new Spanish term:
            text: gato
            translation: cat
        And I visit "/review/index"
        Then the review dashboard shows 0 due cards
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # Nothing has been graded yet, so Undo must not be on screen at
        # all -- not merely flagged hidden in the markup.
        And the undo button is hidden
        When I reveal the review answer
        And I grade the card "Good"
        Then the review session is done
        And the undo button is available
        When I undo the last grade
        Then the review card shows the term "gato"


    Scenario: A card is pronounced in the language of its term
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        Given I visit "/review/session"
        Then the review card offers pronunciation
        When I click the speaker on the review card
        # Not the browser's own language: the queue holds the terms of
        # every language the user studies, so each card says which one it
        # is spoken in.
        Then the term "gato" is spoken as "es-ES"


    Scenario: A card is not read out until its answer is revealed
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # The term is on the front, but the page does not say it: the user
        # has to recall the meaning first, and the answer is where the
        # word is pronounced.  The page has been interacted with by now
        # (creating the term was a click, and Chromium's activation is
        # sticky across navigations), so nothing but the new rule is
        # keeping it quiet.
        And nothing was spoken
        When I reveal the review answer
        Then the term "gato" is spoken as "es-ES"


    Scenario: An untouched page speaks at the reveal, not before it
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        And the page has not been interacted with
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        # The reveal is the only thing that speaks, and it does not wait
        # for the document to have been activated: Chrome and Safari only
        # gate the utterance the page asks for on its own, and the key
        # press that reveals is a gesture anyway.
        And nothing was spoken
        When I press the space key in the review session
        Then the term "gato" is spoken as "es-ES"


    Scenario: Turning the pronunciation off keeps the cards quiet
        Given a new Spanish term:
            text: gato
            translation: cat
        And I record what the review session pronounces
        # Explicit keyword: a bare And inherits the previous one (Given),
        # and the step is registered as a When.
        When I turn off the card pronunciation in the review settings
        # Untouched as well: the switch has to silence the reveal on the
        # un-activated page too, not just on the one already interacted
        # with.
        Given the page has not been interacted with
        Given I visit "/review/session"
        Then the review card shows the term "gato"
        When I press the space key in the review session
        Then nothing was spoken
